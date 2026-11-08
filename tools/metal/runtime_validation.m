/* Optional, bounded hardware validation of the actual Metal frontend.
 * Compiled only by build_nezha_statusbar.py --metal-validation, and run only
 * with nezha_metal_validation=1 in SpringBoard. Normal shim builds exclude it.
 * Each phase records real failures and output pixels; no application content,
 * user settings, or wallpaper configuration is replaced by these checks. */
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#import <MetalPerformanceShaders/MetalPerformanceShaders.h>
#import <CoreImage/CoreImage.h>
#if defined(NEZHA_IMAGE_DECODE_VALIDATION)
#import <ImageIO/ImageIO.h>
#include "image_decode_plan.h"
#endif
#import <IOSurface/IOSurfaceRef.h>
#include <dlfcn.h>
#include <mach/kern_return.h>
#include <sys/sysctl.h>
#include <time.h>
#include <unistd.h>
#include "validation_air.h"

static NSMutableDictionary *receipt;
static NSString *outputDirectory;
static unsigned failures;
static BOOL gpuTimedOut;

static uint64_t nanos(clockid_t clock)
{
    struct timespec value;
    if (clock_gettime(clock, &value)) return 0;
    return (uint64_t)value.tv_sec * 1000000000ULL + (uint64_t)value.tv_nsec;
}

static void persist(NSString *phase)
{
    receipt[@"phase"] = phase;
    receipt[@"failures"] = @(failures);
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:&error];
    if (!json || ![json writeToFile:[outputDirectory stringByAppendingPathComponent:@"receipt.json"] options:NSDataWritingAtomic error:&error])
        NSLog(@"NEZHA validation: receipt write failed: %@", error);
}

static BOOL require(BOOL condition, NSString *message)
{
    if (!condition) {
        failures++;
        [(NSMutableArray *)receipt[@"errors"] addObject:message];
        NSLog(@"NEZHA validation: FAIL %@", message);
    }
    return condition;
}

static BOOL finish(id<MTLCommandBuffer> command, NSString *label)
{
    if (!require(command != nil, [label stringByAppendingString:@": nil command buffer"])) return NO;
    dispatch_semaphore_t done = dispatch_semaphore_create(0);
    [command addCompletedHandler:^(id<MTLCommandBuffer> buffer) { (void)buffer; dispatch_semaphore_signal(done); }];
    [command commit];
    if (!require(dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 8LL * NSEC_PER_SEC)) == 0,
                 [label stringByAppendingString:@": GPU completion exceeded 8 seconds"])) { gpuTimedOut = YES; return NO; }
    return require(command.status == MTLCommandBufferStatusCompleted,
                   [NSString stringWithFormat:@"%@: status=%lu error=%@", label, (unsigned long)command.status, command.error]);
}

static NSData *pixels(id<MTLDevice> device, id<MTLTexture> texture, NSString *label)
{
    if (!texture) return nil;
    NSUInteger row = texture.width * 4, length = row * texture.height;
    id<MTLBuffer> output = [device newBufferWithLength:length options:MTLResourceStorageModeShared];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    if (!require(output && command && blit, [label stringByAppendingString:@": readback allocation failed"])) return nil;
    [blit copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0, 0, 0)
              sourceSize:MTLSizeMake(texture.width, texture.height, 1) toBuffer:output destinationOffset:0
      destinationBytesPerRow:row destinationBytesPerImage:length];
    [blit endEncoding];
    if (!finish(command, label)) return nil;
    NSData *data = [NSData dataWithBytes:output.contents length:length];
    NSError *error = nil;
    if (![data writeToFile:[outputDirectory stringByAppendingPathComponent:[label stringByAppendingString:@".bgra"]]
                  options:NSDataWritingAtomic error:&error]) require(NO, [NSString stringWithFormat:@"readback save: %@", error]);
    return data;
}

static void heapLifecycle(id<MTLDevice> device)
{
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatR16Float
                                                                                     width:281 height:91 mipmapped:NO];
    descriptor.storageMode = MTLStorageModePrivate;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    MTLSizeAndAlign answer = [device heapTextureSizeAndAlignWithDescriptor:descriptor];
    if (!require(answer.size && answer.align && !(answer.align & (answer.align - 1)), @"MPS heap query has no valid layout")) return;
    MTLHeapDescriptor *heapDescriptor = [MTLHeapDescriptor new];
    heapDescriptor.storageMode = MTLStorageModePrivate;
    heapDescriptor.size = ((answer.size * 2 + answer.align - 1) / answer.align) * answer.align;
    id<MTLHeap> heap = [device newHeapWithDescriptor:heapDescriptor];
    id<MTLTexture> texture = [heap newTextureWithDescriptor:descriptor];
    if (!require(heap && texture, @"MPS automatic heap texture allocation failed")) return;
    NSUInteger used = heap.usedSize, allocated = texture.allocatedSize;
    BOOL before = texture.isAliasable;
    [texture makeAliasable];
    BOOL after = texture.isAliasable;
    NSUInteger freed = heap.usedSize;
    [texture makeAliasable];
    require(!before && after && freed < used && heap.usedSize == freed, @"heap aliasability did not return its range exactly once");
    require(allocated >= answer.size, @"heap texture allocatedSize is below its queried range");
    require(texture.hazardTrackingMode == MTLHazardTrackingModeUntracked, @"default heap hazard mode did not resolve to untracked");
    id<MTLTexture> reused = [heap newTextureWithDescriptor:descriptor];
    require(reused != nil, @"returned automatic heap range cannot be reused");
    MTLSizeAndAlign bufferAnswer = [device heapBufferSizeAndAlignWithLength:4096 options:MTLResourceStorageModePrivate];
    require(bufferAnswer.size >= 4096 && bufferAnswer.align > 0, @"buffer heap size query failed");
    receipt[@"heap"] = @{@"size":@(answer.size), @"alignment":@(answer.align), @"used_before":@(used),
                         @"used_after_alias":@(freed), @"texture_allocated":@(allocated), @"reused":@(reused != nil)};
}

static id<MTLTexture> colorTarget(id<MTLDevice> device)
{
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                     width:64 height:64 mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite | MTLTextureUsageRenderTarget;
    return [device newTextureWithDescriptor:descriptor];
}

static void coreImageGradient(id<MTLDevice> device)
{
    id<MTLTexture> target = colorTarget(device);
    CIContext *context = [CIContext contextWithMTLDevice:device options:@{kCIContextUseSoftwareRenderer:@NO}];
    CIFilter *filter = [CIFilter filterWithName:@"CILinearGradient" withInputParameters:@{
        @"inputPoint0":[CIVector vectorWithX:0 Y:0], @"inputPoint1":[CIVector vectorWithX:64 Y:0],
        @"inputColor0":[CIColor colorWithRed:1 green:0 blue:0 alpha:1],
        @"inputColor1":[CIColor colorWithRed:0 green:0 blue:1 alpha:1]}];
    if (!require(target && context && filter.outputImage, @"Core Image gradient setup failed")) return;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceLinearSRGB);
    [context render:filter.outputImage toMTLTexture:target commandBuffer:command bounds:CGRectMake(0, 0, 64, 64) colorSpace:space];
    CGColorSpaceRelease(space);
    if (!finish(command, @"ci-gradient-render")) return;
    NSData *data = pixels(device, target, @"ci-gradient");
    if (!data) return;
    const uint8_t *bytes = data.bytes;
    unsigned mismatches = 0;
    for (NSUInteger y = 8; y < 56; y += 8) for (NSUInteger x = 8; x < 56; x += 8) {
        const uint8_t *pixel = bytes + (y * 64 + x) * 4;
        int blue = (int)((x + 0.5) * 255.0 / 64.0), red = 255 - blue;
        if (abs((int)pixel[0] - blue) > 10 || pixel[1] > 10 || abs((int)pixel[2] - red) > 10 || pixel[3] < 245) mismatches++;
    }
    require(!mismatches, [NSString stringWithFormat:@"Core Image generated gradient has %u incorrect sample points", mismatches]);
    receipt[@"core_image"] = @{@"incorrect_points":@(mismatches), @"width":@64, @"height":@64};
}

static void mpsBlur(id<MTLDevice> device)
{
    id<MTLTexture> input = colorTarget(device), output = colorTarget(device);
    if (!require(input && output, @"MPS image allocation failed")) return;
    uint8_t image[64 * 64 * 4];
    for (NSUInteger y = 0; y < 64; y++) for (NSUInteger x = 0; x < 64; x++) {
        uint8_t *pixel = image + (y * 64 + x) * 4;
        pixel[0] = x >= 32 ? 255 : 0;
        pixel[1] = y >= 32 ? 255 : 0;
        pixel[2] = x < 32 ? 255 : 0;
        pixel[3] = 255;
    }
    [input replaceRegion:MTLRegionMake2D(0, 0, 64, 64) mipmapLevel:0 withBytes:image bytesPerRow:256];
    MPSImageGaussianBlur *blur = [[MPSImageGaussianBlur alloc] initWithDevice:device sigma:2.0];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    [blur encodeToCommandBuffer:command sourceTexture:input destinationTexture:output];
    if (!finish(command, @"mps-blur-render")) return;
    NSData *data = pixels(device, output, @"mps-blur");
    if (!data) return;
    unsigned mismatches = 0;
    for (NSUInteger y = 12; y < 64; y += 40) for (NSUInteger x = 12; x < 64; x += 40) {
        const uint8_t *actual = (const uint8_t *)data.bytes + (y * 64 + x) * 4;
        const uint8_t *expected = image + (y * 64 + x) * 4;
        for (unsigned c = 0; c < 4; c++) if (abs((int)actual[c] - expected[c]) > 8) mismatches++;
    }
    require(!mismatches, [NSString stringWithFormat:@"MPS blur has %u incorrect interior channels", mismatches]);
    receipt[@"mps"] = @{@"incorrect_channels":@(mismatches), @"width":@64, @"height":@64};
}

/* The compositor's observed narrow surface has a 608-byte pitch, which the A840
 * cannot represent as a direct 128-byte-pitched image. Exercise the real shadow
 * render/copyback path and verify its padding, rather than only tiled textures. */
static void coreImageSurface(id<MTLDevice> device)
{
    const NSUInteger width = 151, height = 79, row = 608;
    IOSurfaceRef surface = IOSurfaceCreate((__bridge CFDictionaryRef)@{
        (id)kIOSurfaceWidth:@(width), (id)kIOSurfaceHeight:@(height),
        (id)kIOSurfaceBytesPerElement:@4, (id)kIOSurfaceBytesPerRow:@(row),
        (id)kIOSurfacePixelFormat:@(0x42475241u), (id)kIOSurfaceAllocSize:@(row * height)});
    if (!require(surface != NULL, @"narrow IOSurface allocation failed")) return;
    @try {
        if (!require(IOSurfaceGetBytesPerRow(surface) == row, @"IOSurface did not preserve the observed narrow pitch")) return;
        if (!require(IOSurfaceLock(surface, 0, NULL) == KERN_SUCCESS, @"IOSurface initial lock failed")) return;
        memset(IOSurfaceGetBaseAddress(surface), 0x5a, row * height);
        if (!require(IOSurfaceUnlock(surface, 0, NULL) == KERN_SUCCESS, @"IOSurface initial unlock failed")) return;
        MTLTextureDescriptor *descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                         width:width height:height mipmapped:NO];
        descriptor.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
        descriptor.storageMode = MTLStorageModeShared;
        id<MTLTexture> target = [device newTextureWithDescriptor:descriptor iosurface:surface plane:0];
        CIContext *context = [CIContext contextWithMTLDevice:device options:@{kCIContextUseSoftwareRenderer:@NO}];
        CIFilter *filter = [CIFilter filterWithName:@"CILinearGradient" withInputParameters:@{
            @"inputPoint0":[CIVector vectorWithX:0 Y:0], @"inputPoint1":[CIVector vectorWithX:width Y:0],
            @"inputColor0":[CIColor colorWithRed:1 green:0 blue:0 alpha:1],
            @"inputColor1":[CIColor colorWithRed:0 green:0 blue:1 alpha:1]}];
        if (!require(target && context && filter.outputImage, @"narrow CI surface setup failed")) return;
        id<MTLCommandQueue> queue = [device newCommandQueue];
        id<MTLCommandBuffer> command = [queue commandBuffer];
        CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceLinearSRGB);
        [context render:filter.outputImage toMTLTexture:target commandBuffer:command bounds:CGRectMake(0, 0, width, height) colorSpace:space];
        CGColorSpaceRelease(space);
        if (!finish(command, @"ci-surface-render")) return;
        if (!require(IOSurfaceLock(surface, kIOSurfaceLockReadOnly, NULL) == KERN_SUCCESS, @"IOSurface readback lock failed")) return;
        unsigned mismatches = 0, padding = 0;
        const uint8_t *bytes = IOSurfaceGetBaseAddress(surface);
        for (NSUInteger y = 8; y < height - 8; y += 8) for (NSUInteger x = 8; x < width - 8; x += 8) {
            const uint8_t *pixel = bytes + y * row + x * 4;
            int blue = (int)((x + 0.5) * 255.0 / width), red = 255 - blue;
            if (abs((int)pixel[0] - blue) > 10 || pixel[1] > 10 || abs((int)pixel[2] - red) > 10 || pixel[3] < 245) mismatches++;
        }
        for (NSUInteger y = 0; y < height; y++) for (NSUInteger x = width * 4; x < row; x++) if (bytes[y * row + x] != 0x5a) padding++;
        require(!mismatches && !padding, [NSString stringWithFormat:@"narrow CI surface incorrect samples=%u padding=%u", mismatches, padding]);
        require(IOSurfaceUnlock(surface, kIOSurfaceLockReadOnly, NULL) == KERN_SUCCESS, @"IOSurface readback unlock failed");
        receipt[@"core_image_surface"] = @{@"incorrect_points":@(mismatches), @"incorrect_padding_bytes":@(padding), @"width":@(width), @"height":@(height), @"row":@(row)};
        pixels(device, target, @"ci-surface");
    } @finally { CFRelease(surface); }
}

/* Exercise MPS's own temporary-image allocator and release, not just our direct
 * heap calls. The texture form of MPSImageGaussianBlur does not adjust readCount;
 * the caller must return the temporary after its final encoded read (SDK contract). */
static void mpsTemporaryChain(id<MTLDevice> device)
{
    const NSUInteger width = 281, height = 91;
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm
                                                                                     width:width height:height mipmapped:NO];
    descriptor.storageMode = MTLStorageModeShared;
    descriptor.usage = MTLTextureUsageShaderRead | MTLTextureUsageShaderWrite;
    id<MTLTexture> input = [device newTextureWithDescriptor:descriptor], output = [device newTextureWithDescriptor:descriptor];
    NSMutableData *colors = [NSMutableData dataWithLength:width * height * 4];
    if (!require(input && output && colors, @"MPS temporary-chain image allocation failed")) return;
    for (NSUInteger i = 0; i < width * height; i++) ((uint32_t *)colors.mutableBytes)[i] = 0xff4080c0u;
    [input replaceRegion:MTLRegionMake2D(0, 0, width, height) mipmapLevel:0 withBytes:colors.bytes bytesPerRow:width * 4];
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    if (!require(command != nil, @"MPS temporary-chain command buffer allocation failed")) return;
    descriptor.pixelFormat = MTLPixelFormatRGBA16Float;
    descriptor.storageMode = MTLStorageModePrivate;
    MPSTemporaryImage *temporary = [MPSTemporaryImage temporaryImageWithCommandBuffer:command textureDescriptor:descriptor];
    id<MTLTexture> middle = temporary.texture;
    if (!require(temporary && middle, @"MPS temporary texture allocation failed")) return;
    BOOL heap = middle.heap != nil;
    NSUInteger allocated = middle.allocatedSize;
    @try {
        MPSImageGaussianBlur *first = [[MPSImageGaussianBlur alloc] initWithDevice:device sigma:12.0];
        MPSImageGaussianBlur *second = [[MPSImageGaussianBlur alloc] initWithDevice:device sigma:3.0];
        first.edgeMode = MPSImageEdgeModeClamp;
        second.edgeMode = MPSImageEdgeModeClamp;
        [first encodeToCommandBuffer:command sourceTexture:input destinationTexture:middle];
        [second encodeToCommandBuffer:command sourceTexture:middle destinationTexture:output];
    } @finally { temporary.readCount = 0; }
    if (!finish(command, @"mps-temporary-render")) return;
    NSData *data = pixels(device, output, @"mps-temporary");
    if (!data) return;
    unsigned mismatches = 0;
    const uint8_t expected[4] = {192, 128, 64, 255};
    for (NSUInteger y = 16; y < height - 16; y += 16) for (NSUInteger x = 32; x < width - 32; x += 32) {
        const uint8_t *pixel = (const uint8_t *)data.bytes + (y * width + x) * 4;
        for (unsigned c = 0; c < 4; c++) if (abs((int)pixel[c] - expected[c]) > 4) mismatches++;
    }
    require(!mismatches, [NSString stringWithFormat:@"MPS temporary chain has %u incorrect channels", mismatches]);
    receipt[@"mps_temporary"] = @{@"incorrect_channels":@(mismatches), @"heap_used":@(heap), @"allocated_size":@(allocated),
                                  @"read_count_after_release":@(temporary.readCount), @"width":@(width), @"height":@(height)};
}

static int compareTimes(const void *a, const void *b)
{
    uint64_t left = *(const uint64_t *)a, right = *(const uint64_t *)b;
    return left < right ? -1 : left > right;
}

static NSDictionary *timingSummary(uint64_t *values, unsigned count)
{
    if (!count) return @{@"count":@0};
    uint64_t total = 0;
    for (unsigned i = 0; i < count; i++) total += values[i];
    qsort(values, count, sizeof *values, compareTimes);
    return @{@"count":@(count), @"mean_ns":@(total / count), @"p50_ns":@(values[count / 2]),
             @"p90_ns":@(values[(count - 1) * 9 / 10]), @"max_ns":@(values[count - 1])};
}

@protocol NZValidationLayout
- (NSUInteger)size;
- (void)initializeTextureMemory:(void *)memory;
- (void)copyFromLinearBytes:(const void *)bytes linearOffset:(NSUInteger)offset
         linearBytesPerRow:(NSUInteger)row linearBytesPerImage:(NSUInteger)image
           toTextureMemory:(void *)memory textureSlice:(NSUInteger)slice textureLevel:(NSUInteger)level textureRegion:(MTLRegion)region;
@end
@protocol NZValidationDevice
- (id<NZValidationLayout>)newTextureLayoutWithDescriptor:(MTLTextureDescriptor *)descriptor isHeapOrBufferBacked:(BOOL)backed;
- (id<MTLTexture>)newTextureWithBytesNoCopy:(void *)bytes length:(NSUInteger)length descriptor:(MTLTextureDescriptor *)descriptor
                              deallocator:(void (^)(void *, NSUInteger))deallocator;
@end

/* The thumbnail's exact geometry and our ImageIO layout ABI, with authored ASTC
 * constant blocks. This tests serialization, partial edge blocks, upload and GPU
 * sampling without changing any saved wallpaper or reusing its already-black data. */
static void astcThumbnail(id<MTLDevice> device)
{
    const NSUInteger width = 302, height = 656, columns = (width + 3) / 4, rows = (height + 3) / 4;
    MTLTextureDescriptor *descriptor = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatASTC_4x4_LDR
                                                                                     width:width height:height mipmapped:NO];
    descriptor.usage = MTLTextureUsageShaderRead;
    descriptor.storageMode = MTLStorageModeShared;
    id<NZValidationLayout> layout = [(id<NZValidationDevice>)device newTextureLayoutWithDescriptor:descriptor isHeapOrBufferBacked:NO];
    if (!require(layout != nil && layout.size >= columns * rows * 16, @"thumbnail texture layout allocation failed")) return;
    void *memory = NULL;
    if (!require(posix_memalign(&memory, 16, layout.size) == 0, @"thumbnail serialized memory allocation failed")) return;
    NSMutableData *blocks = [NSMutableData dataWithLength:columns * rows * 16];
    if (!require(blocks != nil, @"thumbnail ASTC block allocation failed")) { free(memory); return; }
    for (NSUInteger y = 0; y < rows; y++) for (NSUInteger x = 0; x < columns; x++) {
        uint8_t *block = (uint8_t *)blocks.mutableBytes + (y * columns + x) * 16;
        const uint8_t mode[8] = {0xfc, 0xfd, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
        memcpy(block, mode, sizeof mode);
        uint16_t color[4] = {x < columns / 2 ? 65535 : 0, 0, x < columns / 2 ? 0 : 65535, 65535};
        memcpy(block + 8, color, sizeof color);
    }
    [layout initializeTextureMemory:memory];
    [layout copyFromLinearBytes:blocks.bytes linearOffset:0 linearBytesPerRow:columns * 16 linearBytesPerImage:blocks.length
               toTextureMemory:memory textureSlice:0 textureLevel:0 textureRegion:MTLRegionMake2D(0, 0, width, height)];
    id<MTLTexture> source = [(id<NZValidationDevice>)device newTextureWithBytesNoCopy:memory length:layout.size descriptor:descriptor
                                                                      deallocator:^(void *bytes, NSUInteger length) { (void)length; free(bytes); }];
    if (!require(source != nil, @"thumbnail marked-memory texture creation failed")) { free(memory); return; }
    typedef id<MTLLibrary> (*AirLibrary)(const void *, size_t);
    AirLibrary load = (AirLibrary)dlsym(RTLD_DEFAULT, "native_bridge_metal_library_with_air");
    if (!require(load != NULL, @"thumbnail AIR loader is unavailable")) return;
    id<MTLLibrary> library = load(validation_air, sizeof validation_air);
    MTLRenderPipelineDescriptor *pipelineDescriptor = [MTLRenderPipelineDescriptor new];
    pipelineDescriptor.vertexFunction = [library newFunctionWithName:@"quad_vs"];
    pipelineDescriptor.fragmentFunction = [library newFunctionWithName:@"tex_fs"];
    pipelineDescriptor.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    NSError *error = nil;
    id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:pipelineDescriptor error:&error];
    if (!require(pipeline != nil, [NSString stringWithFormat:@"thumbnail pipeline: %@", error])) return;
    id<MTLTexture> target = colorTarget(device);
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    MTLRenderPassDescriptor *render = [MTLRenderPassDescriptor renderPassDescriptor];
    render.colorAttachments[0].texture = target;
    render.colorAttachments[0].loadAction = MTLLoadActionClear;
    render.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:render];
    if (!require(target && encoder, @"thumbnail render target or encoder allocation failed")) return;
    [encoder setRenderPipelineState:pipeline];
    [encoder setFragmentTexture:source atIndex:0];
    [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    [encoder endEncoding];
    if (!finish(command, @"astc-thumbnail-render")) return;
    NSData *data = pixels(device, target, @"astc-thumbnail");
    if (!data) return;
    unsigned mismatches = 0;
    for (unsigned y = 8; y < 64; y += 16) for (unsigned x = 8; x < 64; x += 16) {
        const uint8_t *pixel = (const uint8_t *)data.bytes + (y * 64 + x) * 4;
        BOOL blue = x >= 32;
        if (abs((int)pixel[0] - (blue ? 255 : 0)) > 8 || pixel[1] > 8 ||
            abs((int)pixel[2] - (blue ? 0 : 255)) > 8 || pixel[3] < 247) mismatches++;
    }
    require(!mismatches, [NSString stringWithFormat:@"ASTC thumbnail has %u incorrect sample points", mismatches]);
    receipt[@"astc_thumbnail"] = @{@"incorrect_points":@(mismatches), @"width":@(width), @"height":@(height), @"layout_bytes":@(layout.size)};
}

/* Every draw's output remains visible. A final full-screen draw alone would hide
 * accidentally updating a descriptor set referenced by an earlier draw. */
static void descriptorStripes(id<MTLDevice> device)
{
    typedef id<MTLLibrary> (*AirLibrary)(const void *, size_t);
    AirLibrary load = (AirLibrary)dlsym(RTLD_DEFAULT, "native_bridge_metal_library_with_air");
    if (!require(load != NULL, @"stripe AIR loader is unavailable")) return;
    id<MTLLibrary> library = load(validation_air, sizeof validation_air);
    MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction = [library newFunctionWithName:@"quad_vs"];
    descriptor.fragmentFunction = [library newFunctionWithName:@"tex_fs"];
    descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    NSError *error = nil;
    id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
    if (!require(pipeline != nil, [NSString stringWithFormat:@"stripe pipeline: %@", error])) return;
    id<MTLTexture> target = colorTarget(device), textures[2] = {colorTarget(device), colorTarget(device)};
    if (!require(target && textures[0] && textures[1], @"stripe texture allocation failed")) return;
    uint32_t colors[64 * 64];
    for (unsigned texture = 0; texture < 2; texture++) {
        for (unsigned i = 0; i < 64 * 64; i++) colors[i] = texture ? 0xff00ff00u : 0xffff0000u;
        [textures[texture] replaceRegion:MTLRegionMake2D(0, 0, 64, 64) mipmapLevel:0 withBytes:colors bytesPerRow:256];
    }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    MTLRenderPassDescriptor *render = [MTLRenderPassDescriptor renderPassDescriptor];
    render.colorAttachments[0].texture = target;
    render.colorAttachments[0].loadAction = MTLLoadActionClear;
    render.colorAttachments[0].storeAction = MTLStoreActionStore;
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:render];
    if (!require(encoder != nil, @"stripe encoder allocation failed")) return;
    [encoder setRenderPipelineState:pipeline];
    for (unsigned stripe = 0; stripe < 16; stripe++) {
        [encoder setFragmentTexture:textures[stripe & 1] atIndex:0];
        [encoder setScissorRect:(MTLScissorRect){stripe * 4, 0, 4, 64}];
        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
    }
    [encoder endEncoding];
    if (!finish(command, @"descriptor-stripes-render")) return;
    NSData *data = pixels(device, target, @"descriptor-stripes");
    if (!data) return;
    unsigned mismatches = 0;
    for (unsigned y = 0; y < 64; y++) for (unsigned x = 0; x < 64; x++) {
        const uint8_t *pixel = (const uint8_t *)data.bytes + (y * 64 + x) * 4;
        BOOL green = (x / 4) & 1;
        if (pixel[0] > 8 || abs((int)pixel[1] - (green ? 255 : 0)) > 8 ||
            abs((int)pixel[2] - (green ? 0 : 255)) > 8 || pixel[3] < 247) mismatches++;
    }
    require(!mismatches, [NSString stringWithFormat:@"immutable descriptor stripes have %u incorrect pixels", mismatches]);
    receipt[@"descriptor_stripes"] = @{@"incorrect_pixels":@(mismatches), @"stripes":@16};
}

/* A resolving pass must write the single-sample destination, including a nonzero
 * mip and array slice. A valid render into the MSAA attachment alone is insufficient. */
static void multisampleResolve(id<MTLDevice> device)
{
    if (!require([device supportsTextureSampleCount:4], @"4-sample resolve is unavailable on this device")) return;
    MTLTextureDescriptor *multisample = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:64 height:64 mipmapped:NO];
    multisample.textureType = MTLTextureType2DMultisample;
    multisample.sampleCount = 4;
    multisample.storageMode = MTLStorageModePrivate;
    multisample.usage = MTLTextureUsageRenderTarget;
    id<MTLTexture> input = [device newTextureWithDescriptor:multisample];
    MTLTextureDescriptor *single = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatBGRA8Unorm width:128 height:128 mipmapped:YES];
    single.textureType = MTLTextureType2DArray;
    single.arrayLength = 2;
    single.storageMode = MTLStorageModePrivate;
    single.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> output = [device newTextureWithDescriptor:single];
    id<MTLBuffer> readback = [device newBufferWithLength:64 * 64 * 4 options:MTLResourceStorageModeShared];
    if (!require(input && output && readback, @"MSAA resolve resources unavailable")) return;
    id<MTLCommandQueue> queue = [device newCommandQueue];
    id<MTLCommandBuffer> command = [queue commandBuffer];
    MTLRenderPassDescriptor *pass = [MTLRenderPassDescriptor renderPassDescriptor];
    pass.colorAttachments[0].texture = input;
    pass.colorAttachments[0].loadAction = MTLLoadActionClear;
    pass.colorAttachments[0].clearColor = MTLClearColorMake(0.25, 0.5, 0.75, 1);
    pass.colorAttachments[0].storeAction = MTLStoreActionMultisampleResolve;
    pass.colorAttachments[0].resolveTexture = output;
    pass.colorAttachments[0].resolveLevel = 1;
    pass.colorAttachments[0].resolveSlice = 1;
    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:pass];
    if (!require(encoder != nil, @"MSAA resolve encoder unavailable")) return;
    [encoder endEncoding];
    id<MTLBlitCommandEncoder> blit = [command blitCommandEncoder];
    [blit copyFromTexture:output sourceSlice:1 sourceLevel:1 sourceOrigin:MTLOriginMake(0, 0, 0) sourceSize:MTLSizeMake(64, 64, 1)
                toBuffer:readback destinationOffset:0 destinationBytesPerRow:256 destinationBytesPerImage:16384];
    [blit endEncoding];
    if (!finish(command, @"multisample-resolve")) return;
    const uint8_t expected[4] = {191, 128, 64, 255};
    const uint8_t *bytes = readback.contents;
    unsigned incorrect = 0;
    for (unsigned i = 0; i < 64 * 64; i++) for (unsigned c = 0; c < 4; c++)
        if (abs((int)bytes[i * 4 + c] - expected[c]) > 2) incorrect++;
    require(!incorrect, [NSString stringWithFormat:@"multisample resolve destination has %u incorrect channels", incorrect]);
    receipt[@"multisample_resolve"] = @{@"incorrect_channels":@(incorrect), @"samples":@4, @"resolve_level":@1, @"resolve_slice":@1};
}

#if defined(NEZHA_IMAGE_DECODE_VALIDATION)
/* Read-only decoding of explicit source assets implicated by a real display defect.
 * Pixel outputs are compared on the host with independently decoded references;
 * successful CGImage creation alone is never reported as verified image content.
 *
 * data-a-live: Clownfish's alpha-bearing HEIC layers read back as all-zero BGRA
 * on this device while the host decodes the same files fine, and unified logs
 * show no decoder error. Pixels alone cannot separate three mutually exclusive
 * causes: ImageIO returned an alpha-only/empty CGImage (alphaInfo 7 / no data),
 * CGBitmapContext drew nothing because its bitmap layout mismatched the image,
 * or the HEIF decoder produced no pixels at all. Record the CGImage's real
 * metadata and a copy of its own data provider (which bypasses CGContext) so a
 * host comparison can tell them apart. This substitutes no image, changes no
 * setting, and reports no success it did not observe. */
static void imageIODecode(id<MTLDevice> device)
{
    (void)device;
    NSError *error = nil;
    NSArray *plan = [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:image_decode_plan length:sizeof image_decode_plan] options:0 error:&error];
    if (!require([plan isKindOfClass:NSArray.class] && plan.count && plan.count <= 3, @"invalid source-image diagnostic plan")) return;
    NSMutableArray *results = [NSMutableArray array];
    for (unsigned i = 0; i < plan.count; i++) {
        NSDictionary *entry = plan[i];
        NSString *path = entry[@"source"];
        if (!require([path isKindOfClass:NSString.class] && [path hasPrefix:@"/Library/Wallpaper/Collections/"], @"source-image path is not a collection asset")) return;
        NSURL *url = [NSURL fileURLWithPath:path];
        CGImageSourceRef source = CGImageSourceCreateWithURL((__bridge CFURLRef)url, NULL);
        if (!require(source != NULL, [@"ImageIO source open failed: " stringByAppendingString:path])) continue;
        /* Source-level facts must be read before the source is released. */
        NSString *uti = nil;
        CFStringRef sourceType = CGImageSourceGetType(source);
        if (sourceType) uti = (__bridge NSString *)sourceType;
        NSNumber *sourceHasAlpha = nil;
        CFDictionaryRef properties = CGImageSourceCopyPropertiesAtIndex(source, 0, NULL);
        if (properties) {
            CFBooleanRef hasAlpha = (CFBooleanRef)CFDictionaryGetValue(properties, kCGImagePropertyHasAlpha);
            if (hasAlpha && CFGetTypeID(hasAlpha) == CFBooleanGetTypeID()) sourceHasAlpha = @(CFBooleanGetValue(hasAlpha));
            CFRelease(properties);
        }
        CGImageRef image = CGImageSourceCreateImageAtIndex(source, 0, (__bridge CFDictionaryRef)@{(id)kCGImageSourceShouldCacheImmediately:@YES});
        CFRelease(source);
        if (!require(image != NULL, [@"ImageIO decode failed: " stringByAppendingString:path])) continue;
        NSUInteger width = CGImageGetWidth(image), height = CGImageGetHeight(image);
        require(width == [entry[@"width"] unsignedLongLongValue] && height == [entry[@"height"] unsignedLongLongValue], @"ImageIO decoded extent differs from source metadata");

        CGColorSpaceRef imageSpace = CGImageGetColorSpace(image);
        NSMutableDictionary *meta = [@{@"alpha_info":@(CGImageGetAlphaInfo(image)),
                                       @"bitmap_info":@(CGImageGetBitmapInfo(image)),
                                       @"bits_per_component":@(CGImageGetBitsPerComponent(image)),
                                       @"bits_per_pixel":@(CGImageGetBitsPerPixel(image)),
                                       @"color_space_model":@(imageSpace ? CGColorSpaceGetModel(imageSpace) : -1)} mutableCopy];
        if (uti) meta[@"uti"] = uti;
        if (sourceHasAlpha) meta[@"source_has_alpha"] = sourceHasAlpha;
        if (imageSpace) {
            CFStringRef imageSpaceName = CGColorSpaceCopyName(imageSpace);
            if (imageSpaceName) meta[@"color_space_name"] = CFBridgingRelease(imageSpaceName);
        }
        /* The image's own pixels, without any CGContext conversion or blend. */
        CGDataProviderRef provider = CGImageGetDataProvider(image);
        CFDataRef raw = provider ? CGDataProviderCopyData(provider) : NULL;
        NSData *rawPixels = raw ? (NSData *)CFBridgingRelease(raw) : nil;
        if (rawPixels) {
            meta[@"provider_bytes"] = @(rawPixels.length);
            NSString *rawName = [NSString stringWithFormat:@"imageio-raw-%u.data", i];
            if (require([rawPixels writeToFile:[outputDirectory stringByAppendingPathComponent:rawName] options:NSDataWritingAtomic error:&error],
                        @"CGImage raw pixel output write failed")) meta[@"provider_output"] = rawName;
        } else {
            require(NO, @"CGImage data provider copy failed");
        }

        uint8_t bytes[64 * 64 * 4] = {};
        CGColorSpaceRef space = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        CGContextRef context = space ? CGBitmapContextCreate(bytes, 64, 64, 8, 256, space, kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little) : NULL;
        if (space) CGColorSpaceRelease(space);
        if (!context) { CGImageRelease(image); require(NO, @"ImageIO readback bitmap allocation failed"); return; }
        CGContextSetBlendMode(context, kCGBlendModeCopy);
        CGContextSetInterpolationQuality(context, kCGInterpolationHigh);
        CGContextDrawImage(context, CGRectMake(0, 0, 64, 64), image);
        CGContextRelease(context); CGImageRelease(image);
        unsigned mn[4] = {255, 255, 255, 255}, mx[4] = {0, 0, 0, 0};
        NSUInteger nonzero = 0;
        for (NSUInteger p = 0; p < 64 * 64; p++) {
            BOOL anyChannel = NO;
            for (unsigned c = 0; c < 4; c++) {
                uint8_t value = bytes[p * 4 + c];
                if (value < mn[c]) mn[c] = value;
                if (value > mx[c]) mx[c] = value;
                if (value) anyChannel = YES;
            }
            if (anyChannel) nonzero++;
        }
        meta[@"drawn_channel_min"] = @[@(mn[0]), @(mn[1]), @(mn[2]), @(mn[3])];
        meta[@"drawn_channel_max"] = @[@(mx[0]), @(mx[1]), @(mx[2]), @(mx[3])];
        meta[@"drawn_nonzero_pixels"] = @(nonzero);
        NSString *name = [NSString stringWithFormat:@"imageio-%u.bgra", i];
        NSData *pixels = [NSData dataWithBytes:bytes length:sizeof bytes];
        require([pixels writeToFile:[outputDirectory stringByAppendingPathComponent:name] options:NSDataWritingAtomic error:&error], @"ImageIO diagnostic output write failed");
        [results addObject:@{@"source":path, @"width":@(width), @"height":@(height), @"output":name,
                             @"image":meta, @"pixels_independently_verified":@NO}];
    }
    receipt[@"image_io_decode"] = results;
}
#endif

/* Fixed, authored compositor-shaped work: one full-screen textured rectangle per draw,
 * texture/scissor changes, several passes per command buffer, and separate encode/commit
 * timings. Warmup is excluded. This measures the same native frontend on the real A840;
 * it does not claim to reproduce a complete UIKit frame or count submissions as FPS. */
static void encodingCost(id<MTLDevice> device)
{
    typedef id<MTLLibrary> (*AirLibrary)(const void *, size_t);
    AirLibrary load = (AirLibrary)dlsym(RTLD_DEFAULT, "native_bridge_metal_library_with_air");
    if (!require(load != NULL, @"authored AIR loader is unavailable")) return;
    id<MTLLibrary> library = load(validation_air, sizeof validation_air);
    MTLRenderPipelineDescriptor *descriptor = [MTLRenderPipelineDescriptor new];
    descriptor.vertexFunction = [library newFunctionWithName:@"quad_vs"];
    descriptor.fragmentFunction = [library newFunctionWithName:@"tex_fs"];
    descriptor.colorAttachments[0].pixelFormat = MTLPixelFormatBGRA8Unorm;
    NSError *error = nil;
    id<MTLRenderPipelineState> pipeline = [device newRenderPipelineStateWithDescriptor:descriptor error:&error];
    if (!require(pipeline != nil, [NSString stringWithFormat:@"encode workload pipeline: %@", error])) return;
    id<MTLTexture> target = colorTarget(device), textures[2] = {colorTarget(device), colorTarget(device)};
    if (!require(target && textures[0] && textures[1], @"encode workload textures unavailable")) return;
    uint32_t colors[64 * 64];
    for (unsigned texture = 0; texture < 2; texture++) {
        for (unsigned i = 0; i < 64 * 64; i++) colors[i] = texture ? 0xff00ff00u : 0xffff0000u;
        [textures[texture] replaceRegion:MTLRegionMake2D(0, 0, 64, 64) mipmapLevel:0 withBytes:colors bytesPerRow:256];
    }
    id<MTLCommandQueue> queue = [device newCommandQueue];
    enum { FRAMES = 8, WARM = 2, BUFFERS = 4, PASSES = 4, DRAWS = 24, MEASURED = FRAMES - WARM };
    uint64_t opens[MEASURED * BUFFERS * PASSES], ends[MEASURED * BUFFERS * PASSES];
    uint64_t draws[MEASURED * BUFFERS * PASSES * DRAWS], commits[MEASURED * BUFFERS];
    uint64_t frameWall[MEASURED], frameCpu[MEASURED];
    unsigned openCount = 0, endCount = 0, drawCount = 0, commitCount = 0;
    for (unsigned frame = 0; frame < FRAMES; frame++) {
        uint64_t began = nanos(CLOCK_MONOTONIC), cpu = nanos(CLOCK_THREAD_CPUTIME_ID);
        for (unsigned buffer = 0; buffer < BUFFERS; buffer++) {
            @autoreleasepool {
                id<MTLCommandBuffer> command = [queue commandBuffer];
                for (unsigned pass = 0; pass < PASSES; pass++) {
                    MTLRenderPassDescriptor *render = [MTLRenderPassDescriptor renderPassDescriptor];
                    render.colorAttachments[0].texture = target;
                    render.colorAttachments[0].loadAction = MTLLoadActionClear;
                    render.colorAttachments[0].storeAction = MTLStoreActionStore;
                    uint64_t start = nanos(CLOCK_MONOTONIC);
                    id<MTLRenderCommandEncoder> encoder = [command renderCommandEncoderWithDescriptor:render];
                    if (frame >= WARM) opens[openCount++] = nanos(CLOCK_MONOTONIC) - start;
                    if (!require(encoder != nil, @"encode workload render encoder unavailable")) return;
                    [encoder setRenderPipelineState:pipeline];
                    for (unsigned draw = 0; draw < DRAWS; draw++) {
                        start = nanos(CLOCK_MONOTONIC);
                        [encoder setFragmentTexture:textures[draw & 1] atIndex:0];
                        [encoder setScissorRect:(MTLScissorRect){draw == DRAWS - 1 ? 0 : draw, 0, draw == DRAWS - 1 ? 64 : 64 - draw, 64}];
                        [encoder drawPrimitives:MTLPrimitiveTypeTriangle vertexStart:0 vertexCount:3];
                        if (frame >= WARM) draws[drawCount++] = nanos(CLOCK_MONOTONIC) - start;
                    }
                    start = nanos(CLOCK_MONOTONIC);
                    [encoder endEncoding];
                    if (frame >= WARM) ends[endCount++] = nanos(CLOCK_MONOTONIC) - start;
                }
                dispatch_semaphore_t done = dispatch_semaphore_create(0);
                [command addCompletedHandler:^(id<MTLCommandBuffer> completed) { (void)completed; dispatch_semaphore_signal(done); }];
                uint64_t start = nanos(CLOCK_MONOTONIC);
                [command commit];
                if (frame >= WARM) commits[commitCount++] = nanos(CLOCK_MONOTONIC) - start;
                if (!require(dispatch_semaphore_wait(done, dispatch_time(DISPATCH_TIME_NOW, 8LL * NSEC_PER_SEC)) == 0,
                             @"encode workload GPU completion timed out")) { gpuTimedOut = YES; return; }
                if (!require(command.status == MTLCommandBufferStatusCompleted,
                             [NSString stringWithFormat:@"encode workload command failed: %@", command.error])) return;
            }
        }
        if (frame >= WARM) { frameWall[frame - WARM] = nanos(CLOCK_MONOTONIC) - began; frameCpu[frame - WARM] = nanos(CLOCK_THREAD_CPUTIME_ID) - cpu; }
    }
    NSData *readback = pixels(device, target, @"encoding-output");
    if (readback) {
        const uint8_t *pixel = (const uint8_t *)readback.bytes + (32 * 64 + 32) * 4;
        require(pixel[0] < 8 && pixel[1] > 247 && pixel[2] < 8 && pixel[3] > 247, @"encode workload final texture was not rendered green");
    }
    receipt[@"encoding"] = @{@"passes_per_frame":@(BUFFERS * PASSES), @"draws_per_frame":@(BUFFERS * PASSES * DRAWS),
        @"pass_open":timingSummary(opens, openCount), @"draw":timingSummary(draws, drawCount),
        @"pass_end":timingSummary(ends, endCount), @"commit":timingSummary(commits, commitCount),
        @"frame_wall_including_gpu":timingSummary(frameWall, MEASURED), @"encoding_thread_cpu":timingSummary(frameCpu, MEASURED)};
}

static void runValidation(void)
{
    @autoreleasepool {
        char boot[128] = {}; size_t size = sizeof boot;
        if (sysctlbyname("kern.bootsessionuuid", boot, &size, NULL, 0)) snprintf(boot, sizeof boot, "pid-%d", getpid());
        outputDirectory = [@"/private/var/mobile/Library/Logs/NezhaMetalValidation" stringByAppendingPathComponent:@(boot)];
        NSError *error = nil;
        if (![[NSFileManager defaultManager] createDirectoryAtPath:outputDirectory withIntermediateDirectories:YES attributes:nil error:&error]) {
            NSLog(@"NEZHA validation: output directory failed: %@", error); return;
        }
        receipt = [@{@"validation_revision":@5, @"errors":[NSMutableArray array], @"pid":@(getpid()), @"boot":@(boot), @"start_ns":@(nanos(CLOCK_MONOTONIC))} mutableCopy];
        persist(@"starting");
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!require(device != nil, @"Metal discovery returned no device")) { persist(@"failed"); return; }
        receipt[@"device"] = device.name ?: @"unnamed";
        const struct { const char *name; void (*run)(id<MTLDevice>); } phases[] = {
            {"heap-lifecycle", heapLifecycle}, {"core-image-gradient", coreImageGradient}, {"mps-blur", mpsBlur},
            {"core-image-surface", coreImageSurface}, {"astc-thumbnail", astcThumbnail},
            {"descriptor-stripes", descriptorStripes}, {"encoding-cost", encodingCost}, {"mps-temporary", mpsTemporaryChain}, {"multisample-resolve", multisampleResolve},
#if defined(NEZHA_IMAGE_DECODE_VALIDATION)
            {"image-io-decode", imageIODecode},
#endif
        };
        for (unsigned i = 0; i < sizeof phases / sizeof phases[0]; i++) {
#if defined(NEZHA_IMAGE_DECODE_VALIDATION)
            /* This build answers one question: why alpha-bearing HEIC layers read
             * back empty. Skip the unrelated GPU phases at runtime, but keep the
             * shared array intact so no phase becomes dead code in this build. */
            if (strcmp(phases[i].name, "image-io-decode")) continue;
#endif
            persist(@(phases[i].name));
            @try { phases[i].run(device); }
            @catch (NSException *exception) { require(NO, [NSString stringWithFormat:@"%s: %@: %@", phases[i].name, exception.name, exception.reason]); }
            if (gpuTimedOut) break;
        }
        receipt[@"end_ns"] = @(nanos(CLOCK_MONOTONIC));
        persist(failures ? @"failed" : @"completed");
        NSLog(@"NEZHA validation: completed failures=%u output=%@", failures, outputDirectory);
    }
}

__attribute__((constructor)) static void beginValidation(void)
{
    if (strcmp(getprogname(), "SpringBoard")) return;
#if !defined(NEZHA_IMAGE_DECODE_VALIDATION)
    /* The ordinary opt-in validation build keeps a second, runtime gate: even an
     * explicitly built validation shim stays inert unless the trial asks for it. */
    char arguments[2048] = {}; size_t size = sizeof arguments - 1;
    if (sysctlbyname("kern.bootargs", arguments, &size, NULL, 0) || !strstr(arguments, "nezha_metal_validation=1")) return;
#else
    /* The ImageIO diagnostic shim is compiled only by an explicit
     * build_nezha_statusbar.py --metal-validation --image-decode-plan call and
     * installed for one bounded trial. Adding nezha_metal_validation=1 on top of
     * the full Wi-Fi/FairPlay service baseline exceeds the kernel's 608-byte
     * boot-args field, so this build is enabled at compile time instead. A normal
     * shim contains no validation code at all, so the default remains off. */
#endif
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 24LL * NSEC_PER_SEC), dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ runValidation(); });
}
