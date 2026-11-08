#!/usr/bin/env python3
"""Adapt UniXNU AdrenoMetal (formerly NativeMetal; pinned ce5e19e, written for iPadOS 17 on an Adreno 740) to iOS 16.7 on the
Adreno 840 (data-a-live). Exact, asserted text edits in a NativeMetal checkout; run once on a fresh tree.

  adapt_ios16.py NATIVEMETAL_DIR

1. NativeAdrenoMetalPlugin.mm: without NativeAdrenoMetal.publish the plugin used to start its stage-1
   self-test (raw ABI and PM4 tests written for the A740) in every process that runs Metal discovery.
   Here it returns the client device to processes named in NativeAdrenoMetal.clients and nil to all
   others, backboardd included; nothing else runs.
2. reportDisplayState writes into backboardd at iPadOS 17.7.11 QuartzCore addresses: disabled.
3. NativeBridgeMetalFrontend.mm: _MTLDevice is 528 bytes on iOS 16.7 (536 on iPadOS 17.7.11, the only
   size accepted); the dynamic subclass adds no instance variables, so both are accepted.
4. NativeTurnipSmoke.h: Turnip next to the plugin binary, else /usr/lib/libvulkan_freedreno.dylib
   (data-a-live installs it there for tools/vktest; no second 17 MB copy inside the bundle).
"""
import pathlib
import sys

ROOT = pathlib.Path(sys.argv[1])


def edit(name, old, new):
    p = ROOT / name
    s = p.read_text(encoding='utf-8')
    assert s.count(old) == 1, (name, old[:70])
    p.write_text(s.replace(old, new), encoding='utf-8')
    print('edited', name)


edit('NativeAdrenoMetalPlugin.mm',
     """#include <pthread.h>
""",
     """#include <pthread.h>
#include <sys/sysctl.h>
""")
edit('NativeAdrenoMetalPlugin.mm',
     """    if (publish::requested()) return publish::deviceForPort(port);
    static bool attempted;""",
     """    if (publish::requested()) return publish::deviceForPort(port);
    // data-a-live (iOS 16, Adreno 840): no stage-1 self-test, it drives the A740's raw ABI. A process named in
    // NativeAdrenoMetal.clients gets its own device, every other process (backboardd too) nil and its software renderer.
    {   // data-a-live: TU_DEBUG from the boot-arg pandora_tu_debug=a,b,c when given (bb37), else nocb; a value
        // already in the environment wins either way (mtltest picks its own per launch). Without sysmem Turnip
        // chooses per render pass between on-chip tiles (GMEM) and memory (its autotune), its default on other
        // Adrenos; nocb keeps concurrent binning off (gen8 BV/BR stalls seen elsewhere, not yet understood here).
        static char chosen[128];
        const char *flags = "nocb";
        char args[1024] = {};
        size_t size = sizeof args - 1;
        if (sysctlbyname("kern.bootargs", args, &size, nullptr, 0) == 0)
            if (const char *at = strstr(args, "pandora_tu_debug=")) {
                snprintf(chosen, sizeof chosen, "%.*s", (int)strcspn(at + 17, " "), at + 17);
                flags = chosen;
            }
        setenv("TU_DEBUG", flags, 0);
        static bool told;
        if (!told) {
            told = true;
            NSLog(@"pandora-metal TU_DEBUG=%s", getenv("TU_DEBUG"));
        }
    }
    if (port) return publish::clientDevice(port);
    static bool attempted;""")
edit('bridge/NativeMetalObjects.rs',
     """#[unsafe(no_mangle)]
pub extern "C" fn nmb_command_buffer_commit(command_buffer: *mut NmbCommandBuffer) -> bool {
    guard(false, || {
        get(command_buffer)?.0.commit().map_err(e("commit"))?;
        Ok(true)
    })
}""",
     """// data-a-live: what a completed command buffer's execution status reported beyond a clean run, through syslog (the
// unified log), the first 64 per process: resident-set misses (a stored pointer that reached no resident allocation,
// or null), a read of an argument function constants removed, a device replay fault, a clamped indirect count, a
// cubic argument sampler. post18's hang followed two GPU reads of address 0 by the Astronomy poster.
unsafe extern "C" {
    fn syslog(priority: i32, format: *const c_char, ...);
}

fn nmb_report_execution(done: &CommandBuffer) {
    use std::sync::atomic::{AtomicU32, Ordering};
    static REPORTED: AtomicU32 = AtomicU32::new(0);
    let Some(report) = done.execution_report() else { return };
    if !(report.resident_miss || report.absent_argument_read || report.replay_fault || report.overflow
        || report.cubic_argument_sampler)
    {
        return;
    }
    if REPORTED.fetch_add(1, Ordering::Relaxed) >= 64 {
        return;
    }
    let text = format!(
        "pandora-metal: execution report: resident misses {} ({}), absent argument read {}, replay fault {} ({:#x}), \\
         clamped {}, cubic argument sampler {}\\0",
        report.resident_misses, report.resident_miss, report.absent_argument_read, report.replay_fault,
        report.replay_faults.0, report.overflow, report.cubic_argument_sampler
    );
    unsafe { syslog(3, c"%s".as_ptr(), text.as_ptr() as *const c_char) };
}

#[unsafe(no_mangle)]
pub extern "C" fn nmb_command_buffer_commit(command_buffer: *mut NmbCommandBuffer) -> bool {
    guard(false, || {
        let command_buffer = &get(command_buffer)?.0;
        let _ = command_buffer.add_completed_handler(Box::new(nmb_report_execution));
        command_buffer.commit().map_err(e("commit"))?;
        Ok(true)
    })
}""")
edit('NativeAdrenoMetalPlugin.mm',
     """static id clientDevice(unsigned port) {
    if (!listedClient()) return nil;""",
     """static id clientDevice(unsigned port) {
    if (!listedClient()) {
        // data-a-live: which processes ask for Metal without a device (towards every process having one, as
        // on an iPhone): logged once per process.
        static bool told;
        if (!told) {
            told = true;
            NSLog(@"pandora-metal: %s asked for a device, not in NativeAdrenoMetal.clients", getprogname());
        }
        return nil;
    }""")
edit('NativeAdrenoMetalPlugin.mm',
     """static void reportDisplayState(Gpu &gpu) {
    ++gReports;""",
     """static void reportDisplayState(Gpu &gpu) {
    if (gReports >= 0) return;   // data-a-live: the addresses below are iPadOS 17.7.11 QuartzCore's, not iOS 16's
    ++gReports;""")
edit('NativeAdrenoMetalPlugin.mm',
     """        if (native_bridge_metal_first_problem(problem, sizeof problem) && problem[0]) clientFailure(port, "problem", problem);
    }
    return device;""",
     """        if (native_bridge_metal_first_problem(problem, sizeof problem) && problem[0]) clientFailure(port, "problem", problem);
    }
    if (device && !strcmp(getprogname(), "backboardd")) {
        // data-a-live: the render server as a client (no publish mode here): what it reads for the deep
        // framebuffer, and the bridge's telemetry (refused surfaces, last problems) every 30 s.
        CFTypeRef deep = CFPreferencesCopyAppValue(CFSTR("CAEnableDeepFramebuffer"), kCFPreferencesCurrentApplication);
        NSLog(@"pandora-metal: backboardd has a device; CAEnableDeepFramebuffer %@", (__bridge id)deep);
        if (deep) CFRelease(deep);
        pthread_t thread;
        if (pthread_create(&thread, nullptr, [](void *) -> void * {
                static char table[6144];
                for (unsigned round = 0; round < 10; ++round) {
                    sleep(30);
                    native_bridge_metal_telemetry(table, sizeof table);
                    for (char *line = table, *end; *line; line = end) {
                        end = strchr(line, '\\n');
                        if (end) *end++ = 0; else end = line + strlen(line);
                        NSLog(@"pandora-metal T%u %s", round, line);
                    }
                }
                return nullptr;
            }, nullptr) == 0) pthread_detach(thread);
    }
    return device;""")
edit('NativeBridgeMetalFrontend.mm',
     """static NSError *nbError(NSString *why) {
 return""",
     """static NSError *nbError(NSString *why) {
 NSLog(@"pandora-metal refused: %@", why);   // data-a-live: every refusal in the log (backboardd: specialize, bb7)
 return""")
edit('NativeBridgeMetalFrontend.mm',
     """  case 84:case 88:return 32;
  default:return 0;""",
     """  case 84:case 88:return 32;
  // data-a-live: 3-wide vectors, lanes packed as metal2vulkan's canon/fc.rs reads them (the first
  // lanes*width bytes; Metal's storage is at least that). iOS 16 backboardd specializes a uchar3 (bb8).
  case 5:case 31:case 35:return 12;
  case 18:case 39:case 43:return 6;
  case 47:case 51:case 55:return 3;
  case 83:case 87:return 24;
  default:return 0;""")
edit('NativeBridgeMetalFrontend.mm',
     """ (void)bytes;(void)length;(void)d;(void)deallocator;
 NBLOG(@"native-metal: newTextureWithBytesNoCopy:length:descriptor:deallocator: is not implemented (Apple's texture layout of raw memory is not known)");
 return nil;""",
     """ // data-a-live: a one-level 2D texture in rows of texels or blocks (bb25: QuartzCore in backboardd asks for the App
 // Library's full-screen ASTC 4x4 image, shader-read only, 282 x 609 blocks of 16 bytes). Made as an ordinary texture
 // and filled from the bytes once; the caller's deallocator runs when the texture goes. A read-only texture sees no
 // later writes to the bytes, which a texture over them would.
 static const uint8_t astc[16][2]={{4,2},{4,4},{5,4},{5,5},{6,5},{6,6},{8,4},{8,5},{8,6},{8,8},{10,5},{10,6},{10,8},{10,10},{12,10},{12,12}};
 const NSUInteger format=d.pixelFormat;
 NSUInteger bw=1,bh=1,bb=0;
 if(format>=185 && format<=200){bw=astc[format-185][0];bh=astc[format-185][1];bb=16;}
 else if(format>=203 && format<=218){bw=astc[format-203][0];bh=astc[format-203][1];bb=16;}
 else switch(format){case 1:case 10:bb=1;break;case 30:bb=2;break;case 70:case 71:case 80:case 81:bb=4;break;case 115:bb=8;break;default:break;}
 const NSUInteger rowBytes=(d.width+bw-1)/bw*bb,rows=(d.height+bh-1)/bh;
 if(bytes && bb && d.textureType==MTLTextureType2D && d.mipmapLevelCount==1 && d.arrayLength==1 && d.depth==1 &&
    d.sampleCount==1 && length>=rowBytes*rows) {
  id<MTLTexture> texture=[(id<MTLDevice>)self newTextureWithDescriptor:d];
  if(texture) {
   [texture replaceRegion:MTLRegionMake2D(0,0,d.width,d.height) mipmapLevel:0 withBytes:bytes bytesPerRow:rowBytes];
   if(deallocator) {
    static char ownerKey;
    NSData *owner=[[NSData alloc] initWithBytesNoCopy:bytes length:length deallocator:^(void *b,NSUInteger l){deallocator(b,l);}];
    objc_setAssociatedObject(texture,&ownerKey,owner,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
   }
   static std::atomic<uint32_t> made{0};
   if(++made<=5)NSLog(@"pandora-metal nocopy: made fmt %lu %lux%lu, %lu bytes per row, copied once",(unsigned long)format,
                      (unsigned long)d.width,(unsigned long)d.height,(unsigned long)rowBytes);
   return texture;
  }
 }
 NBLOG(@"native-metal: newTextureWithBytesNoCopy:length:descriptor:deallocator: is not implemented (Apple's texture layout of raw memory is not known)");
 // data-a-live: what is asked for and still refused: the descriptor, the memory and the callers (image + offset), the
 // first 20 calls and then one in 100.
 static std::atomic<uint32_t> asked{0};
 const uint32_t nth=++asked;
 if(nth<=20 || nth%100==0) {
  void *frames[10];const int n=backtrace(frames,10);
  std::string callers;char item[96];
  for(int i=1;i<n;i++) {
   Dl_info info{};
   if(!dladdr(frames[i],&info) || !info.dli_fname)continue;
   const char *slash=strrchr(info.dli_fname,'/');
   snprintf(item,sizeof item," %s+%lx",slash?slash+1:info.dli_fname,(unsigned long)((uintptr_t)frames[i]-(uintptr_t)info.dli_fbase));
   callers+=item;
  }
  NSLog(@"pandora-metal nocopy #%u: fmt %lu type %lu %lux%lux%lu mips %lu array %lu usage %lx storage %lu opts %lx"
        " bytes %p (page +%lx) length %lu (per row if one plane: %lu) |%s",nth,(unsigned long)d.pixelFormat,
        (unsigned long)d.textureType,(unsigned long)d.width,(unsigned long)d.height,(unsigned long)d.depth,
        (unsigned long)d.mipmapLevelCount,(unsigned long)d.arrayLength,(unsigned long)d.usage,(unsigned long)d.storageMode,
        (unsigned long)d.resourceOptions,bytes,(unsigned long)((uintptr_t)bytes&0x3fff),(unsigned long)length,
        (unsigned long)(d.height?length/d.height:0),callers.c_str());
 }
 return nil;""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(!base || class_getInstanceSize(base)!=536){""",
     """ // data-a-live: 528 bytes on iOS 16.7 (ios-fuxi docs-lanes/metal.md), 536 on iPadOS 17.7.11.
 if(!base || (class_getInstanceSize(base)!=536 && class_getInstanceSize(base)!=528)){""")
edit('NativeTurnipSmoke.h',
     """    snprintf(slash + 1, size - size_t(slash + 1 - path), "libvulkan_freedreno.dylib");
    return true;""",
     """    snprintf(slash + 1, size - size_t(slash + 1 - path), "libvulkan_freedreno.dylib");
    if (access(path, R_OK) != 0) snprintf(path, size, "%s", "/usr/lib/libvulkan_freedreno.dylib");   // data-a-live
    return true;""")

# ---- data-a-live: frame capture for the App Library (bb26) -------------------------------------------------
# Its full-screen blur draws empty (the zoomed home screen snapshot behind shows through, the rest is black),
# with no refusal on record. Right after newTextureWithBytesNoCopy makes the App Library's image, the next
# 800 events backboardd encodes are logged as "pandora-metal cap": render passes (attachments, level, load and
# store actions, clear colour), pipelines (both functions, colour format), textures bound (size, format, mip
# levels, type, view parent, IOSurface), viewports, scissors, draws, texture copies, mipmap generation and
# commits. Twice per boot at most.
edit('NativeBridgeMetalFrontend.mm',
     """#define NBLOG(...) do{NSString *nbText=[NSString stringWithFormat:__VA_ARGS__];NSLog(@"%@",nbText);noteProblem(nbText);}while(0)
""",
     """#define NBLOG(...) do{NSString *nbText=[NSString stringWithFormat:__VA_ARGS__];NSLog(@"%@",nbText);noteProblem(nbText);}while(0)
// data-a-live: the App Library capture (tools/metal/adapt_ios16.py).
static std::atomic<int> gCapture{0};
static char gCapNamesKey;
static inline bool capturing() {return gCapture.load(std::memory_order_relaxed)>0;}
static void capture(NSString *line) {if(gCapture.fetch_sub(1,std::memory_order_relaxed)>0)NSLog(@"pandora-metal cap %@",line);}
static NSString *capId(id object) {return [NSString stringWithFormat:@"%05lx",(unsigned long)(((uintptr_t)(__bridge void *)object>>4)&0xfffff)];}
static NSString *capTexture(id<MTLTexture> t) {
 if(!t)return @"-";
 id<MTLTexture> parent=[(id)t respondsToSelector:@selector(parentTexture)]?t.parentTexture:nil;
 const bool surface=[(id)t respondsToSelector:@selector(iosurface)] && t.iosurface;
 return [NSString stringWithFormat:@"%@ %lux%lu f%lu m%lu t%lu%@%@%@",capId(t),(unsigned long)t.width,(unsigned long)t.height,
  (unsigned long)t.pixelFormat,(unsigned long)t.mipmapLevelCount,(unsigned long)t.textureType,surface?@" surface":@"",
  parent?@" view-of ":@"",parent?capId(parent):@""];
}
""")
edit('NativeBridgeMetalFrontend.mm',
     """   if(++made<=5)NSLog(@"pandora-metal nocopy: made fmt""",
     """   static std::atomic<int> captures{0};
   if(captures.fetch_add(1)<2)gCapture.store(800);
   if(++made<=5)NSLog(@"pandora-metal nocopy: made fmt""")
edit('NativeBridgeMetalFrontend.mm',
     """ NBRenderPipeline *state=[NBRenderPipeline new];state.handle=handle;gPipelines++;if(error)*error=nil;return (id<MTLRenderPipelineState>)state;""",
     """ NBRenderPipeline *state=[NBRenderPipeline new];state.handle=handle;gPipelines++;if(error)*error=nil;
 objc_setAssociatedObject(state,&gCapNamesKey,[NSString stringWithFormat:@"%@ / %@ c0 f%lu blend %d",d.vertexFunction.name,d.fragmentFunction.name,
  (unsigned long)d.colorAttachments[0].pixelFormat,(int)d.colorAttachments[0].blendingEnabled],OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 return (id<MTLRenderPipelineState>)state;""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)setRenderPipelineState:(id<MTLRenderPipelineState>)state {
""",
     """- (void)setRenderPipelineState:(id<MTLRenderPipelineState>)state {
 if(capturing())capture([NSString stringWithFormat:@"pipe %@ %@",capId(state),objc_getAssociatedObject(state,&gCapNamesKey)]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)texture:(id<MTLTexture>)texture index:(NSUInteger)index stage:(uint32_t)stage {
""",
     """- (void)texture:(id<MTLTexture>)texture index:(NSUInteger)index stage:(uint32_t)stage {
 if(capturing())capture([NSString stringWithFormat:@"tex %s%lu %@",stage==1?"v":stage==2?"f":"c",(unsigned long)index,capTexture(texture)]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)setViewport:(MTLViewport)v {
""",
     """- (void)setViewport:(MTLViewport)v {
 if(capturing())capture([NSString stringWithFormat:@"viewport %.1f %.1f %.1fx%.1f",v.originX,v.originY,v.width,v.height]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)setScissorRect:(MTLScissorRect)r {
""",
     """- (void)setScissorRect:(MTLScissorRect)r {
 if(capturing())capture([NSString stringWithFormat:@"scissor %lu %lu %lux%lu",(unsigned long)r.x,(unsigned long)r.y,(unsigned long)r.width,(unsigned long)r.height]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)i baseInstance:(NSUInteger)b {
""",
     """- (void)drawPrimitives:(MTLPrimitiveType)t vertexStart:(NSUInteger)s vertexCount:(NSUInteger)n instanceCount:(NSUInteger)i baseInstance:(NSUInteger)b {
 if(capturing())capture([NSString stringWithFormat:@"draw p%lu v%lu+%lu i%lu",(unsigned long)t,(unsigned long)s,(unsigned long)n,(unsigned long)i]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """                instanceCount:(NSUInteger)i baseVertex:(NSInteger)v baseInstance:(NSUInteger)bi {
""",
     """                instanceCount:(NSUInteger)i baseVertex:(NSInteger)v baseInstance:(NSUInteger)bi {
 if(capturing())capture([NSString stringWithFormat:@"drawidx p%lu n%lu i%lu",(unsigned long)t,(unsigned long)n,(unsigned long)i]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (id<MTLRenderCommandEncoder>)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d {
""",
     """- (id<MTLRenderCommandEncoder>)renderCommandEncoderWithDescriptor:(MTLRenderPassDescriptor *)d {
 if(capturing()) {
  NSMutableString *line=[NSMutableString stringWithFormat:@"pass rt %lux%lu",(unsigned long)d.renderTargetWidth,(unsigned long)d.renderTargetHeight];
  for(unsigned i=0;i<4;i++) {
   MTLRenderPassColorAttachmentDescriptor *c=d.colorAttachments[i];if(!c.texture)continue;
   [line appendFormat:@" | c%u %@ lv%lu sl%lu load%lu store%lu clear %.2f,%.2f,%.2f,%.2f%@",i,capTexture(c.texture),(unsigned long)c.level,
    (unsigned long)c.slice,(unsigned long)c.loadAction,(unsigned long)c.storeAction,c.clearColor.red,c.clearColor.green,c.clearColor.blue,
    c.clearColor.alpha,c.resolveTexture?[@" resolve " stringByAppendingString:capTexture(c.resolveTexture)]:@""];
  }
  if(d.depthAttachment.texture)[line appendFormat:@" | depth %@ load%lu",capTexture(d.depthAttachment.texture),(unsigned long)d.depthAttachment.loadAction];
  if(d.stencilAttachment.texture)[line appendFormat:@" | stencil %@ load%lu",capTexture(d.stencilAttachment.texture),(unsigned long)d.stencilAttachment.loadAction];
  capture(line);
 }
""")
edit('NativeBridgeMetalFrontend.mm',
     """              toTexture:(id<MTLTexture>)d destinationSlice:(NSUInteger)ds destinationLevel:(NSUInteger)dl destinationOrigin:(MTLOrigin)dO {
""",
     """              toTexture:(id<MTLTexture>)d destinationSlice:(NSUInteger)ds destinationLevel:(NSUInteger)dl destinationOrigin:(MTLOrigin)dO {
 if(capturing())capture([NSString stringWithFormat:@"copy %@ lv%lu %lu,%lu %lux%lu -> %@ lv%lu %lu,%lu",capTexture(s),(unsigned long)sl,(unsigned long)so.x,
  (unsigned long)so.y,(unsigned long)z.width,(unsigned long)z.height,capTexture(d),(unsigned long)dl,(unsigned long)dO.x,(unsigned long)dO.y]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)generateMipmapsForTexture:(id<MTLTexture>)t {
""",
     """- (void)generateMipmapsForTexture:(id<MTLTexture>)t {
 if(capturing())capture([NSString stringWithFormat:@"mipmaps %@",capTexture(t)]);
""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(self.committed){NBLOG(@"native-metal: commit twice");return;}
 self.committed=YES;
""",
     """ if(self.committed){NBLOG(@"native-metal: commit twice");return;}
 if(capturing())capture(@"commit");
 self.committed=YES;
""")

# ---- data-a-live: what the App Library's ASTC bytes look like (bb28) ------------------------------------------
# Re-uploading at commit (38391ce) never fired for the App Library (its bytes do not change after the texture is
# made) and froze backboardd in the app switcher, so it is gone. The image still shows a sharp picture in one part
# and black elsewhere. For a block-compressed texture the next lines describe the bytes as rows of blocks: an 8x8
# grid of the image, each cell the share of all-zero blocks (z), ASTC void-extent (single colour) blocks (v) and
# other blocks (o), so the picture's layout can be compared with the screen, and the callers.
edit('NativeBridgeMetalFrontend.mm',
     """   static std::atomic<int> captures{0};""",
     """   if(bb==16) {   // data-a-live: the block layout of the bytes, 8x8 grid
    static std::atomic<int> described{0};
    if(described.fetch_add(1)<6) {
     const uint8_t *blocks=(const uint8_t *)bytes;
     unsigned zero[8][8]={},single[8][8]={},other[8][8]={};
     for(NSUInteger y=0;y<rows;y++)for(NSUInteger x=0;x<rowBytes/16;x++) {
      const uint8_t *b=blocks+y*rowBytes+x*16;
      bool z=true;for(unsigned k=0;k<16;k++)if(b[k]){z=false;break;}
      const unsigned gy=(unsigned)(y*8/rows),gx=(unsigned)(x*8/(rowBytes/16));
      if(z)zero[gy][gx]++;else if(((b[0]|(b[1]<<8))&0x1ff)==0x1fc)single[gy][gx]++;else other[gy][gx]++;
     }
     void *frames[8];const int n=backtrace(frames,8);std::string callers;char item[96];
     for(int i=1;i<n;i++){Dl_info info{};if(!dladdr(frames[i],&info) || !info.dli_fname)continue;const char *slash=strrchr(info.dli_fname,'/');
      snprintf(item,sizeof item," %s+%lx",slash?slash+1:info.dli_fname,(unsigned long)((uintptr_t)frames[i]-(uintptr_t)info.dli_fbase));callers+=item;}
     const unsigned image=(unsigned)described.load();
     NSLog(@"pandora-metal nocopy blocks #%u %lux%lu %lu per row |%s",image,(unsigned long)d.width,(unsigned long)d.height,(unsigned long)rowBytes,callers.c_str());
     for(unsigned gy=0;gy<8;gy++) {
      NSMutableString *line=[NSMutableString string];
      for(unsigned gx=0;gx<8;gx++) {
       const unsigned all=zero[gy][gx]+single[gy][gx]+other[gy][gx];
       [line appendFormat:@" %02u/%02u/%02u",all?zero[gy][gx]*99/all:0,all?single[gy][gx]*99/all:0,all?other[gy][gx]*99/all:0];
      }
      NSLog(@"pandora-metal nocopy grid #%u r%u z/v/o:%@",image,gy,line);
     }
    }
   }
   static std::atomic<int> captures{0};""")

# ---- data-a-live: no-copy memory is cleaned like surfaces (bb30) ---------------------------------------------
# The kernel maps imported user memory for the GPU uncached and leaves the CPU's caches to the importer
# (NativeAdrenoABI.h; pandora_gpu.c flushes every import only with pandora_gpu_flushall=1). NativeMetal cleans
# the IOSurfaces a command buffer uses, but not memory from newBufferWithBytesNoCopy or textures over it, so
# the GPU can read what the CPU has not written back yet (bb30: the Settings icon showed only its top rows).
# Now those ranges are cleaned and invalidated at commit and at completion too, as the surfaces are.
edit('NativeBridgeMetalFrontend.mm',
     """static NBTexture *textureOnBuffer(NBBuffer *buffer,""",
     """// data-a-live: no-copy buffers a command uses, directly or under a texture (tools/metal/adapt_ios16.py).
static std::atomic<uint64_t> gWrappedFlushes{0},gWrappedFlushBytes{0};
static void flushWrapped(NSSet *owners) {
 if(!owners.count)return;
 std::vector<std::pair<uintptr_t,size_t>> ranges;
 for(id o in owners) {
  NBBuffer *b=nil;
  if([o isKindOfClass:[NBBuffer class]])b=o;
  else if([o isKindOfClass:[NBTexture class]])b=((NBTexture *)o).buffer;
  if(b && b.wrapped)ranges.emplace_back(reinterpret_cast<uintptr_t>(b.wrapped),(size_t)[(id<MTLBuffer>)b length]);
 }
 if(ranges.empty())return;
 std::sort(ranges.begin(),ranges.end());
 const auto last=std::unique(ranges.begin(),ranges.end());
 for(auto it=ranges.begin();it!=last;++it){sys_dcache_flush(reinterpret_cast<void *>(it->first),it->second);gWrappedFlushBytes+=it->second;}
 const uint64_t n=++gWrappedFlushes;
 if(n<=3 || n%2000==0)NSLog(@"pandora-metal wrapped: %llu cleanings, %llu MiB in all",(unsigned long long)n,(unsigned long long)(gWrappedFlushBytes.load()>>20));
}
static NBTexture *textureOnBuffer(NBBuffer *buffer,""")
edit('NativeBridgeMetalFrontend.mm',
     """ flushSurfaces(_surfaces);
 // Actual GPU completion""",
     """ flushSurfaces(_surfaces);
 flushWrapped(_resourceOwners);   // data-a-live: no-copy memory too
 // Actual GPU completion""")
edit('NativeBridgeMetalFrontend.mm',
     """  flushSurfaces(_surfaces);
  // Completion owns this reference""",
     """  flushSurfaces(_surfaces);
  flushWrapped(_resourceOwners);   // data-a-live: no-copy memory too
  // Completion owns this reference""")

# ---- data-a-live: refused IOSurface textures in the log (bb30) -----------------------------------------------
# A texture over an IOSurface that the frontend cannot make is only counted (bb30: 148 of 173 refused, the last
# a 1125x2436 two-plane RGB10A8_2P_XR10 surface). Its layer is then not drawn, which could be the App Library's
# missing background. The first refusals now say what they were and who asked.
edit('NativeBridgeMetalFrontend.mm',
     """ std::lock_guard<std::mutex> lock(gProblemLock);gSurfaceLastRefusal=r;
}""",
     """ std::lock_guard<std::mutex> lock(gProblemLock);gSurfaceLastRefusal=r;
 static std::atomic<int> told{0};
 if(told.fetch_add(1)<40) {
  void *frames[8];const int n=backtrace(frames,8);std::string callers;char item[96];
  for(int i=1;i<n;i++){Dl_info info{};if(!dladdr(frames[i],&info) || !info.dli_fname)continue;const char *slash=strrchr(info.dli_fname,'/');
   snprintf(item,sizeof item," %s+%lx",slash?slash+1:info.dli_fname,(unsigned long)((uintptr_t)frames[i]-(uintptr_t)info.dli_fbase));callers+=item;}
  const uint32_t f=r.format;
  NSLog(@"pandora-metal surface refused: step %u '%c%c%c%c' planes %u plane %u pf %u %llux%llu row %llu offset %llu alloc %llu |%s",
        r.step,(char)(f>>24),(char)(f>>16),(char)(f>>8),(char)f,r.planes,r.plane,r.pixelFormat,(unsigned long long)r.width,
        (unsigned long long)r.height,(unsigned long long)r.row,(unsigned long long)r.offset,(unsigned long long)r.allocation,callers.c_str());
 }
}""")

# ---- data-a-live: App Library probe, boot-arg pandora_astc_probe=1 (bb31) -----------------------------------
# While a finger holds the App Library its background is right (QuartzCore blurs live); at rest it is drawn from
# the two pre-blurred ASTC images and comes out transparent. With the boot-arg the left half of each block row of a
# large ASTC image is replaced by opaque red single-colour (void-extent) blocks before the upload: red on the left
# and the picture on the right = all fine; red and transparent = the real blocks are decoded wrong; nothing = the
# drawing or compositing after the upload is at fault.
edit('NativeBridgeMetalFrontend.mm',
     """#include <execinfo.h>
""",
     """#include <execinfo.h>
#include <sys/sysctl.h>
""")
edit('NativeBridgeMetalFrontend.mm',
     """   [texture replaceRegion:MTLRegionMake2D(0,0,d.width,d.height) mipmapLevel:0 withBytes:bytes bytesPerRow:rowBytes];
   if(deallocator) {""",
     """   const int probeLevel=pandoraAstcProbeLevel();const bool probe=probeLevel>=1;
   if(probe && bb==16 && d.width>=512) {
    std::vector<uint8_t> copy((const uint8_t *)bytes,(const uint8_t *)bytes+rowBytes*rows);
    static const uint8_t red[16]={0xfc,0xfd,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0x00,0x00,0x00,0x00,0xff,0xff};
    const NSUInteger blocks=rowBytes/16;
    for(NSUInteger y=0;y<rows;y++)for(NSUInteger x=0;x<blocks/2;x++)memcpy(&copy[y*rowBytes+x*16],red,16);
    static std::atomic<int> handed{0};
    const int handedNth=handed.fetch_add(1);
    // Level 2: every large ASTC image is handed over as BGRA8; level 3: every other one (the first BGRA8, the second
    // ASTC with its left half red), so one run shows both.
    if(probeLevel==2 || (probeLevel>=3 && handedNth%2==0)) {
     // bb35: the ASTC blocks are in the texture exactly as uploaded, yet no red is drawn. Level 2 hands QuartzCore a
     // BGRA8 texture of the same size instead (left half red, right half green): colour on screen = only ASTC
     // sampling is wrong; still transparent = the sampling or the draw is wrong whatever the format.
     MTLTextureDescriptor *plain=[d copy];plain.pixelFormat=MTLPixelFormatBGRA8Unorm;
     id<MTLTexture> swap=[(id<MTLDevice>)self newTextureWithDescriptor:plain];
     if(swap) {
      std::vector<uint32_t> px((size_t)d.width*d.height);
      for(NSUInteger y=0;y<d.height;y++)for(NSUInteger x=0;x<d.width;x++)px[y*d.width+x]=x<d.width/2?0xffff0000u:0xff00ff00u;
      [swap replaceRegion:MTLRegionMake2D(0,0,d.width,d.height) mipmapLevel:0 withBytes:px.data() bytesPerRow:d.width*4];
      texture=swap;
      objc_setAssociatedObject(swap,&gProbeReadbackKey,@YES,OBJC_ASSOCIATION_RETAIN_NONATOMIC);   // read back its targets too
      NSLog(@"pandora-metal astc probe: %lux%lu handed over as BGRA8, left red right green",(unsigned long)d.width,(unsigned long)d.height);
     }
    } else {
    // bb34: no red reached any render target. The first image is uploaded by replaceRegion, the second through a
    // shared buffer and a blit; each is then copied back by the GPU and compared block by block with what went up.
    static std::atomic<int> nth{0};
    const int which=nth.fetch_add(1);
    static id<MTLCommandQueue> queue=[(id<MTLDevice>)self newCommandQueue];
    const NSUInteger size=rowBytes*rows;
    if(which%2==0)
     [texture replaceRegion:MTLRegionMake2D(0,0,d.width,d.height) mipmapLevel:0 withBytes:copy.data() bytesPerRow:rowBytes];
    else {
     id<MTLBuffer> up=[(id<MTLDevice>)self newBufferWithBytes:copy.data() length:size options:MTLResourceStorageModeShared];
     id<MTLCommandBuffer> c=[queue commandBuffer];id<MTLBlitCommandEncoder> b=[c blitCommandEncoder];
     [b copyFromBuffer:up sourceOffset:0 sourceBytesPerRow:rowBytes sourceBytesPerImage:size sourceSize:MTLSizeMake(d.width,d.height,1)
             toTexture:texture destinationSlice:0 destinationLevel:0 destinationOrigin:MTLOriginMake(0,0,0)];
     [b endEncoding];[c commit];[c waitUntilCompleted];
    }
    id<MTLBuffer> back=[(id<MTLDevice>)self newBufferWithLength:size options:MTLResourceStorageModeShared];
    memset(back.contents,0xa5,size);
    id<MTLCommandBuffer> c=[queue commandBuffer];id<MTLBlitCommandEncoder> b=[c blitCommandEncoder];
    [b copyFromTexture:texture sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(d.width,d.height,1)
              toBuffer:back destinationOffset:0 destinationBytesPerRow:rowBytes destinationBytesPerImage:size];
    [b endEncoding];[c commit];[c waitUntilCompleted];
    unsigned same[2]={},zero[2]={},untouched[2]={},other[2]={};
    const uint8_t *got=(const uint8_t *)back.contents;
    for(NSUInteger y=0;y<rows;y++)for(NSUInteger x=0;x<blocks;x++) {
     const uint8_t *g=got+y*rowBytes+x*16,*w=&copy[y*rowBytes+x*16];const int half=x<blocks/2?0:1;
     bool z=true,u=true;for(int k=0;k<16;k++){if(g[k])z=false;if(g[k]!=0xa5)u=false;}
     if(!memcmp(g,w,16))same[half]++;else if(z)zero[half]++;else if(u)untouched[half]++;else other[half]++;
    }
    NSLog(@"pandora-metal astc probe #%d %lux%lu by %s, left %lu of %lu block columns red; read back left same/zero/untouched/other "
          "%u/%u/%u/%u right %u/%u/%u/%u (status %ld)",which,(unsigned long)d.width,(unsigned long)d.height,which%2==0?"replaceRegion":"blit",
          (unsigned long)(blocks/2),(unsigned long)blocks,same[0],zero[0],untouched[0],other[0],same[1],zero[1],untouched[1],other[1],(long)c.status);
    }
   } else
   [texture replaceRegion:MTLRegionMake2D(0,0,d.width,d.height) mipmapLevel:0 withBytes:bytes bytesPerRow:rowBytes];
   if(deallocator) {""")

# ---- data-a-live: App Library readback, same boot-arg (bb32) --------------------------------------------------
# bb32: with the left halves of both ASTC images red, no red reached the screen at all; the user saw the App
# Library as always. So after the upload something is lost. With pandora_astc_probe=1 the render target a large
# ASTC texture is drawn into is now read back when that command buffer completes (off the completion thread) and
# logged as an 8x8 grid of average colours (RGBA hex): red there = the draw works and the compositing loses it.
edit('NativeBridgeMetalFrontend.mm',
     """#include <sys/sysctl.h>
""",
     """#include <sys/sysctl.h>
static int pandoraAstcProbeLevel() {   // data-a-live: boot-arg pandora_astc_probe=N (tools/metal/adapt_ios16.py)
 static const int level=[]{char args[1024]={};size_t size=sizeof args-1;
  if(sysctlbyname("kern.bootargs",args,&size,nullptr,0)!=0)return 0;
  const char *at=strstr(args,"pandora_astc_probe=");return at?atoi(at+19):0;}();
 return level;
}
static bool pandoraAstcProbe() {return pandoraAstcProbeLevel()>=1;}
static char gProbePassTargetKey,gProbeReadbackKey,gProbeSamplerKey,gProbeBindingsKey,gProbePipeKey;
// data-a-live: an encoder's bindings as the probe's draw log prints them (tools/metal/adapt_ios16.py).
static NSMutableDictionary *probeBindings(id encoder) {
 NSMutableDictionary *seen=objc_getAssociatedObject(encoder,&gProbeBindingsKey);
 if(!seen){seen=[NSMutableDictionary dictionary];objc_setAssociatedObject(encoder,&gProbeBindingsKey,seen,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
 return seen;
}
static NSString *probeHex(const void *bytes,NSUInteger length,NSUInteger most) {
 NSMutableString *hex=[NSMutableString stringWithFormat:@"%lu:",(unsigned long)length];
 for(NSUInteger k=0;bytes && k<length && k<most;k++)[hex appendFormat:@"%s%02x",k%4?"":" ",((const uint8_t *)bytes)[k]];
 return hex;
}
static const char *probeStage(uint32_t stage) {return stage==1?"v":stage==2?"f":"c";}
// One binding of the probe's draw log, in pieces of 160 characters (bb36: the kernel log split longer lines and lost
// characters at each split): "replay N: key [i/n] piece".
static void probeLog(NSString *draw,NSString *key,id value) {
 NSString *text=[value description];
 const NSUInteger piece=160,count=MAX((NSUInteger)1,(text.length+piece-1)/piece);
 for(NSUInteger i=0;i<count;i++)
  NSLog(@"pandora-metal replay %@: %@ [%lu/%lu] %@",draw,key,(unsigned long)(i+1),(unsigned long)count,
        [text substringWithRange:NSMakeRange(i*piece,MIN(piece,text.length-i*piece))]);
}
""")
edit('NativeBridgeMetalFrontend.mm',
     """ return [self encoder:[NBRenderEncoder new] handle:self.committed?nullptr:nmb_render_encoder_new(_handle,&pass) what:@"renderCommandEncoder"];""",
     """ id<MTLRenderCommandEncoder> made=[self encoder:[NBRenderEncoder new] handle:self.committed?nullptr:nmb_render_encoder_new(_handle,&pass) what:@"renderCommandEncoder"];
 if(made && pandoraAstcProbe() && d.colorAttachments[0].texture)   // data-a-live: the pass's target, for the readback
  objc_setAssociatedObject(made,&gProbePassTargetKey,d.colorAttachments[0].texture,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 return made;""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(texture && [(NBTexture *)texture surface])[_commandBuffer.surfaces addObject:texture];
 [self check:nmb_encoder_set_texture""",
     """ if(texture && [(NBTexture *)texture surface])[_commandBuffer.surfaces addObject:texture];
 if(texture && pandoraAstcProbe() && ((texture.pixelFormat==MTLPixelFormatASTC_4x4_LDR && texture.width>=512) ||
                                       objc_getAssociatedObject(texture,&gProbeReadbackKey))) {
  id target=objc_getAssociatedObject(self,&gProbePassTargetKey);   // data-a-live: read it back at completion
  if(target) {
   NSMutableSet *targets=objc_getAssociatedObject(_commandBuffer,&gProbeReadbackKey);
   if(!targets){targets=[NSMutableSet set];objc_setAssociatedObject(_commandBuffer,&gProbeReadbackKey,targets,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
   [targets addObject:target];
  }
 }
 [self check:nmb_encoder_set_texture""")
edit('NativeBridgeMetalFrontend.mm',
     """ @synchronized(gInFlight){[gInFlight removeObject:self];}
 NSArray *completedHandlers;""",
     """ @synchronized(gInFlight){[gInFlight removeObject:self];}
 if(NSSet *targets=objc_getAssociatedObject(self,&gProbeReadbackKey)) {   // data-a-live: the App Library readback
  static std::atomic<int> readbacks{0};
  id<MTLCommandQueue> queue=(id<MTLCommandQueue>)self.queue;
  for(id<MTLTexture> target in [targets copy]) {
   if(readbacks.fetch_add(1)>=12)break;
   dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
    const NSUInteger w=target.width,h=target.height,format=target.pixelFormat;
    if(format!=MTLPixelFormatBGRA8Unorm && format!=MTLPixelFormatBGRA8Unorm_sRGB && format!=MTLPixelFormatRGBA8Unorm) {
     NSLog(@"pandora-metal astc readback: target %lux%lu format %lu, not read",(unsigned long)w,(unsigned long)h,(unsigned long)format);return;
    }
    std::vector<uint8_t> pixels(w*h*4,0xa5);
    if(target.storageMode==MTLStorageModePrivate) {
     // Private: the GPU copies it into a shared buffer first (bb33: getBytes refuses private textures).
     id<MTLBuffer> staging=[target.device newBufferWithLength:w*h*4 options:MTLResourceStorageModeShared];
     id<MTLCommandBuffer> copy=[queue commandBuffer];
     id<MTLBlitCommandEncoder> blit=[copy blitCommandEncoder];
     [blit copyFromTexture:target sourceSlice:0 sourceLevel:0 sourceOrigin:MTLOriginMake(0,0,0) sourceSize:MTLSizeMake(w,h,1)
                  toBuffer:staging destinationOffset:0 destinationBytesPerRow:w*4 destinationBytesPerImage:w*h*4];
     [blit endEncoding];[copy commit];[copy waitUntilCompleted];
     if(staging.contents)memcpy(pixels.data(),staging.contents,w*h*4);
    } else
    [target getBytes:pixels.data() bytesPerRow:w*4 fromRegion:MTLRegionMake2D(0,0,w,h) mipmapLevel:0];
    const bool bgra=format!=MTLPixelFormatRGBA8Unorm;
    NSLog(@"pandora-metal astc readback: target %lux%lu format %lu storage %lu%s",(unsigned long)w,(unsigned long)h,(unsigned long)format,
          (unsigned long)target.storageMode,[(NBTexture *)target surface]?" surface":"");
    for(unsigned gy=0;gy<8;gy++) {
     NSMutableString *line=[NSMutableString string];
     for(unsigned gx=0;gx<8;gx++) {
      uint64_t sum[4]={},n=0;
      for(NSUInteger y=gy*h/8;y<(gy+1)*h/8;y+=4)for(NSUInteger x=gx*w/8;x<(gx+1)*w/8;x+=4) {
       const uint8_t *p=&pixels[(y*w+x)*4];
       sum[0]+=p[bgra?2:0];sum[1]+=p[1];sum[2]+=p[bgra?0:2];sum[3]+=p[3];n++;
      }
      if(!n)n=1;
      [line appendFormat:@" %02x%02x%02x%02x",(unsigned)(sum[0]/n),(unsigned)(sum[1]/n),(unsigned)(sum[2]/n),(unsigned)(sum[3]/n)];
     }
     NSLog(@"pandora-metal astc readback r%u:%@",gy,line);
    }
   });
  }
 }
 NSArray *completedHandlers;""")

# ---- data-a-live: sampler states in the log with the probe (bb35) --------------------------------------------
edit('NativeBridgeMetalFrontend.mm',
     """ NmbSampler *handle=nmb_sampler_new(gDevice,&s);""",
     """ if(pandoraAstcProbe()) {   // data-a-live: what QuartzCore samples with (tools/metal/adapt_ios16.py)
  static std::atomic<int> told{0};
  if(told.fetch_add(1)<40)NSLog(@"pandora-metal sampler: min %lu mag %lu mip %lu address %lu/%lu/%lu border %lu normalized %d lod %.1f-%.1f compare %lu",
   (unsigned long)d.minFilter,(unsigned long)d.magFilter,(unsigned long)d.mipFilter,(unsigned long)d.sAddressMode,(unsigned long)d.tAddressMode,
   (unsigned long)d.rAddressMode,(unsigned long)d.borderColor,(int)d.normalizedCoordinates,d.lodMinClamp,d.lodMaxClamp,(unsigned long)d.compareFunction);
 }
 NmbSampler *handle=nmb_sampler_new(gDevice,&s);""")

# ---- data-a-live: what the App Library draw binds, probe level 3 (bb36) ---------------------------------------
# The samplers and fragment bytes bound when a probed texture is bound, for the first draws: with the samplers'
# descriptors (kept on each sampler as it is made) and the first 64 bytes of each fragment bytes binding.
edit('NativeBridgeMetalFrontend.mm',
     """ NBSampler *sampler=[NBSampler new];sampler.handle=handle;return (id<MTLSamplerState>)sampler;""",
     """ NBSampler *sampler=[NBSampler new];sampler.handle=handle;
 if(pandoraAstcProbe())objc_setAssociatedObject(sampler,&gProbeSamplerKey,[NSString stringWithFormat:@"min%lu mag%lu mip%lu addr%lu/%lu border%lu norm%d",
  (unsigned long)d.minFilter,(unsigned long)d.magFilter,(unsigned long)d.mipFilter,(unsigned long)d.sAddressMode,(unsigned long)d.tAddressMode,
  (unsigned long)d.borderColor,(int)d.normalizedCoordinates],OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 return (id<MTLSamplerState>)sampler;""")
edit('NativeBridgeMetalFrontend.mm',
     """ [self check:nmb_encoder_set_sampler(_handle,stage,""",
     """ if(pandoraAstcProbe()) {   // data-a-live: remembered for the probe's draw log
  NSString *what=sampler?objc_getAssociatedObject(sampler,&gProbeSamplerKey):nil;
  probeBindings(self)[[NSString stringWithFormat:@"%ssampler%lu",probeStage(stage),(unsigned long)index]]=what?:(sampler?@"?":@"none");
 }
 [self check:nmb_encoder_set_sampler(_handle,stage,""")
edit('NativeBridgeMetalFrontend.mm',
     """ [self check:nmb_encoder_set_bytes(_handle,stage,bytes,length,(uint32_t)index) what:"setBytes"];""",
     """ if(pandoraAstcProbe() && bytes)   // data-a-live: remembered for the probe's draw log
  probeBindings(self)[[NSString stringWithFormat:@"%sbytes%lu",probeStage(stage),(unsigned long)index]]=probeHex(bytes,length,768);
 [self check:nmb_encoder_set_bytes(_handle,stage,bytes,length,(uint32_t)index) what:"setBytes"];""")
edit('NativeBridgeMetalFrontend.mm',
     """   [targets addObject:target];
  }
 }""",
     """   [targets addObject:target];
  }
  probeBindings(self)[@"armed"]=[NSString stringWithFormat:@"%lux%lu f%lu at %s%lu",(unsigned long)texture.width,
   (unsigned long)texture.height,(unsigned long)texture.pixelFormat,probeStage(stage),(unsigned long)index];
 }""")

# ---- data-a-live: the App Library draw's whole state, for a replay (bb36) -------------------------------------
# With the probe, every binding an encoder makes is remembered (pipeline with its functions' constants and blend
# state, buffers with their first bytes, bytes, samplers, textures); the first draw after a probed texture is bound
# prints it all with the draw's own arguments and, for an indexed draw, its indices. mtltest replays it from this.
edit('NativeBridgeMetalFrontend.mm',
     """ objc_setAssociatedObject(state,&gCapNamesKey,[NSString stringWithFormat:@"%@ / %@ c0 f%lu blend %d",d.vertexFunction.name,d.fragmentFunction.name,
  (unsigned long)d.colorAttachments[0].pixelFormat,(int)d.colorAttachments[0].blendingEnabled],OBJC_ASSOCIATION_RETAIN_NONATOMIC);""",
     """ objc_setAssociatedObject(state,&gCapNamesKey,[NSString stringWithFormat:@"%@ / %@ c0 f%lu blend %d",d.vertexFunction.name,d.fragmentFunction.name,
  (unsigned long)d.colorAttachments[0].pixelFormat,(int)d.colorAttachments[0].blendingEnabled],OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 if(pandoraAstcProbe()) {   // data-a-live: the whole pipeline, for the probe's draw log
  NBFunction *vf=[(id)d.vertexFunction isKindOfClass:[NBFunction class]]?(NBFunction *)d.vertexFunction:nil;
  NBFunction *ff=[(id)d.fragmentFunction isKindOfClass:[NBFunction class]]?(NBFunction *)d.fragmentFunction:nil;
  MTLRenderPipelineColorAttachmentDescriptor *a=d.colorAttachments[0];
  NSMutableString *full=[NSMutableString stringWithFormat:@"v %@ {%s} f %@ {%s} c0 %lu mask %lu blend %d %lu/%lu/%lu %lu/%lu/%lu c1 %lu mask %lu blend %d samples %lu",
   d.vertexFunction.name,vf?vf.constantsText.c_str():"",d.fragmentFunction.name,ff?ff.constantsText.c_str():"",(unsigned long)a.pixelFormat,
   (unsigned long)a.writeMask,(int)a.blendingEnabled,(unsigned long)a.sourceRGBBlendFactor,(unsigned long)a.destinationRGBBlendFactor,
   (unsigned long)a.rgbBlendOperation,(unsigned long)a.sourceAlphaBlendFactor,(unsigned long)a.destinationAlphaBlendFactor,
   (unsigned long)a.alphaBlendOperation,(unsigned long)d.colorAttachments[1].pixelFormat,(unsigned long)d.colorAttachments[1].writeMask,
   (int)d.colorAttachments[1].blendingEnabled,(unsigned long)d.rasterSampleCount];
  if(MTLVertexDescriptor *v=d.vertexDescriptor)for(unsigned i=0;i<31;i++) {
   MTLVertexAttributeDescriptor *at=v.attributes[i];if(at.format==MTLVertexFormatInvalid)continue;
   [full appendFormat:@" a%u=%lu:%lu:%lu:%lu",i,(unsigned long)at.format,(unsigned long)at.offset,(unsigned long)at.bufferIndex,
    (unsigned long)v.layouts[at.bufferIndex<31?at.bufferIndex:0].stride];
  }
  objc_setAssociatedObject(state,&gProbePipeKey,full,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 }""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(capturing())capture([NSString stringWithFormat:@"pipe %@ %@",capId(state),objc_getAssociatedObject(state,&gCapNamesKey)]);
""",
     """ if(capturing())capture([NSString stringWithFormat:@"pipe %@ %@",capId(state),objc_getAssociatedObject(state,&gCapNamesKey)]);
 if(pandoraAstcProbe())probeBindings(self)[@"pipe"]=objc_getAssociatedObject(state,&gProbePipeKey)?:@"?";
""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(buffer)[_commandBuffer.resourceOwners addObject:buffer];
 [self check:nmb_encoder_set_buffer""",
     """ if(buffer)[_commandBuffer.resourceOwners addObject:buffer];
 if(pandoraAstcProbe()) {   // data-a-live: remembered for the probe's draw log, with the first bytes from the offset
  const uint8_t *contents=buffer?(const uint8_t *)buffer.contents:nullptr;
  probeBindings(self)[[NSString stringWithFormat:@"%sbuffer%lu",probeStage(stage),(unsigned long)index]]=!buffer?@"none":
   [NSString stringWithFormat:@"len %lu off %lu %@",(unsigned long)buffer.length,(unsigned long)offset,
    contents && offset<buffer.length?probeHex(contents+offset,buffer.length-offset,768):@"(no contents)"];
 }
 [self check:nmb_encoder_set_buffer""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(capturing())capture([NSString stringWithFormat:@"tex %s%lu %@",stage==1?"v":stage==2?"f":"c",(unsigned long)index,capTexture(texture)]);
""",
     """ if(capturing())capture([NSString stringWithFormat:@"tex %s%lu %@",stage==1?"v":stage==2?"f":"c",(unsigned long)index,capTexture(texture)]);
 if(pandoraAstcProbe())probeBindings(self)[[NSString stringWithFormat:@"%stex%lu",probeStage(stage),(unsigned long)index]]=capTexture(texture);
""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(capturing())capture([NSString stringWithFormat:@"drawidx p%lu n%lu i%lu",(unsigned long)t,(unsigned long)n,(unsigned long)i]);
""",
     """ if(capturing())capture([NSString stringWithFormat:@"drawidx p%lu n%lu i%lu",(unsigned long)t,(unsigned long)n,(unsigned long)i]);
 if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state
  static std::atomic<int> told{0};
  const int nth=told.fetch_add(1);
  if(nth<8) {
   const uint8_t *ic=b?(const uint8_t *)b.contents:nullptr;const NSUInteger isz=k==MTLIndexTypeUInt16?2:4;
   NSLog(@"pandora-metal replay %d: drawIndexed prim %lu count %lu type %lu offset %lu instances %lu baseVertex %ld indices %@",nth,
    (unsigned long)t,(unsigned long)n,(unsigned long)k,(unsigned long)o,(unsigned long)i,(long)v,
    ic && o<b.length?probeHex(ic+o,MIN(n*isz,b.length-o),96):@"(no contents)");
   NSDictionary *seen=probeBindings(self);
   for(NSString *key in [seen.allKeys sortedArrayUsingSelector:@selector(compare:)])probeLog([NSString stringWithFormat:@"%d",nth],key,seen[key]);
  }
  [probeBindings(self) removeObjectForKey:@"armed"];
 }
""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(capturing())capture([NSString stringWithFormat:@"draw p%lu v%lu+%lu i%lu",(unsigned long)t,(unsigned long)s,(unsigned long)n,(unsigned long)i]);
""",
     """ if(capturing())capture([NSString stringWithFormat:@"draw p%lu v%lu+%lu i%lu",(unsigned long)t,(unsigned long)s,(unsigned long)n,(unsigned long)i]);
 if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state
  static std::atomic<int> told{0};
  const int nth=told.fetch_add(1);
  if(nth<8) {
   NSLog(@"pandora-metal replay d%d: draw prim %lu start %lu count %lu instances %lu",nth,(unsigned long)t,(unsigned long)s,(unsigned long)n,(unsigned long)i);
   NSDictionary *seen=probeBindings(self);
   for(NSString *key in [seen.allKeys sortedArrayUsingSelector:@selector(compare:)])probeLog([NSString stringWithFormat:@"d%d",nth],key,seen[key]);
  }
  [probeBindings(self) removeObjectForKey:@"armed"];
 }
""")

# ---- data-a-live: the probed draw's viewport, scissor and pass (bb38) -----------------------------------------
edit('NativeBridgeMetalFrontend.mm',
     """ if(capturing())capture([NSString stringWithFormat:@"viewport %.1f %.1f %.1fx%.1f",v.originX,v.originY,v.width,v.height]);
""",
     """ if(capturing())capture([NSString stringWithFormat:@"viewport %.1f %.1f %.1fx%.1f",v.originX,v.originY,v.width,v.height]);
 if(pandoraAstcProbe())probeBindings(self)[@"viewport"]=[NSString stringWithFormat:@"%g %g %gx%g z %g-%g",v.originX,v.originY,v.width,v.height,v.znear,v.zfar];
""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(capturing())capture([NSString stringWithFormat:@"scissor %lu %lu %lux%lu",(unsigned long)r.x,(unsigned long)r.y,(unsigned long)r.width,(unsigned long)r.height]);
""",
     """ if(capturing())capture([NSString stringWithFormat:@"scissor %lu %lu %lux%lu",(unsigned long)r.x,(unsigned long)r.y,(unsigned long)r.width,(unsigned long)r.height]);
 if(pandoraAstcProbe())probeBindings(self)[@"scissor"]=[NSString stringWithFormat:@"%lu %lu %lux%lu",(unsigned long)r.x,(unsigned long)r.y,(unsigned long)r.width,(unsigned long)r.height];
""")
edit('NativeBridgeMetalFrontend.mm',
     """  objc_setAssociatedObject(made,&gProbePassTargetKey,d.colorAttachments[0].texture,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
""",
     """  objc_setAssociatedObject(made,&gProbePassTargetKey,d.colorAttachments[0].texture,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 if(made && pandoraAstcProbe()) {   // data-a-live: the pass, for the probe's draw log
  NSMutableString *pass=[NSMutableString stringWithFormat:@"rt %lux%lu",(unsigned long)d.renderTargetWidth,(unsigned long)d.renderTargetHeight];
  for(unsigned i=0;i<8;i++) {
   MTLRenderPassColorAttachmentDescriptor *c=d.colorAttachments[i];if(!c.texture)continue;
   [pass appendFormat:@" | c%u %@ load%lu store%lu clear %g,%g,%g,%g",i,capTexture(c.texture),(unsigned long)c.loadAction,
    (unsigned long)c.storeAction,c.clearColor.red,c.clearColor.green,c.clearColor.blue,c.clearColor.alpha];
  }
  if(d.depthAttachment.texture)[pass appendFormat:@" | depth %@",capTexture(d.depthAttachment.texture)];
  if(d.stencilAttachment.texture)[pass appendFormat:@" | stencil %@",capTexture(d.stencilAttachment.texture)];
  probeBindings(made)[@"pass"]=pass;
 }
""")

# ---- data-a-live: what the CPU wrote vs what the GPU reads, for the probed draw (bb39) ------------------------
# bb38 replayed on the PC (metal2vulkan on lavapipe, /opt/work/replay): QuartzCore's shader translates right, but the
# 64 bytes at vertex buffer 2 + 208 (its mvp matrix) as captured at setVertexBuffer are no matrix, every triangle has
# no area and nothing is drawn; a real matrix there draws the image. QuartzCore may write the matrix after binding.
# Now, for the first probed draws, each buffer bound to the draw is logged (64 bytes from its offset) at the draw, at
# commit and after completion from the CPU, and after completion as the GPU reads it (a blit into a fresh buffer), with
# what kind of buffer it is. The texture's own descriptor (usage, storage, swizzle) is logged where it is made.
edit('NativeBridgeMetalFrontend.mm',
     """#include <sys/sysctl.h>
static int pandoraAstcProbeLevel""",
     """#include <sys/sysctl.h>
#include <objc/message.h>
static int pandoraAstcProbeLevel""")
edit('NativeBridgeMetalFrontend.mm',
     """static const char *probeStage(uint32_t stage) {return stage==1?"v":stage==2?"f":"c";}""",
     """static const char *probeStage(uint32_t stage) {return stage==1?"v":stage==2?"f":"c";}
static char gProbeWatchKey;
static NSString *probeBufferKind(id buffer) {
 NSMutableString *s=[NSMutableString stringWithFormat:@"len %lu storage %lu",(unsigned long)[(id<MTLBuffer>)buffer length],
  (unsigned long)[(id<MTLBuffer>)buffer storageMode]];
 SEL wrapped=NSSelectorFromString(@"wrapped");
 if([buffer respondsToSelector:wrapped] && ((void *(*)(id,SEL))objc_msgSend)(buffer,wrapped))[s appendString:@" no-copy"];
 if([(id<MTLBuffer>)buffer heap])[s appendString:@" heap"];
 return s;
}""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(pandoraAstcProbe()) {   // data-a-live: remembered for the probe's draw log, with the first bytes from the offset
""",
     """ if(pandoraAstcProbe())   // data-a-live: the buffer itself, for the probe's CPU/GPU comparison
  probeBindings(self)[[NSString stringWithFormat:@"~%sbuffer%lu",probeStage(stage),(unsigned long)index]]=buffer?@[buffer,@(offset)]:@[];
 if(pandoraAstcProbe()) {   // data-a-live: remembered for the probe's draw log, with the first bytes from the offset
""")
edit('NativeBridgeMetalFrontend.mm',
     """   NSDictionary *seen=probeBindings(self);
   for(NSString *key in [seen.allKeys sortedArrayUsingSelector:@selector(compare:)])probeLog([NSString stringWithFormat:@"%d",nth],key,seen[key]);
  }""",
     """   NSDictionary *seen=probeBindings(self);
   for(NSString *key in [seen.allKeys sortedArrayUsingSelector:@selector(compare:)])
    if(![key hasPrefix:@"~"])probeLog([NSString stringWithFormat:@"%d",nth],key,seen[key]);
   // the buffers as the CPU holds them now; watched again at commit and after completion (the GPU's view too)
   NSMutableArray *watch=objc_getAssociatedObject(self.commandBuffer,&gProbeWatchKey);
   if(!watch){watch=[NSMutableArray array];objc_setAssociatedObject(self.commandBuffer,&gProbeWatchKey,watch,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
   for(NSString *key in seen)if([key hasPrefix:@"~"] && [seen[key] count]==2) {
    id<MTLBuffer> buffer=seen[key][0];const NSUInteger offset=[seen[key][1] unsignedLongValue];
    const uint8_t *c=(const uint8_t *)buffer.contents;
    NSString *label=[NSString stringWithFormat:@"%d %@+%lu",nth,[key substringFromIndex:1],(unsigned long)offset];
    NSLog(@"pandora-metal watch %@ at draw: %@ | %@",label,probeBufferKind(buffer),c && offset<buffer.length?probeHex(c+offset,buffer.length-offset,64):@"(no contents)");
    [watch addObject:@[buffer,@(offset),label]];
   }
  }""")
edit('NativeBridgeMetalFrontend.mm',
     """  flushSurfaces(_surfaces);
  flushWrapped(_resourceOwners);   // data-a-live: no-copy memory too
  // Completion owns this reference""",
     """  flushSurfaces(_surfaces);
  flushWrapped(_resourceOwners);   // data-a-live: no-copy memory too
  for(NSArray *w in objc_getAssociatedObject(self,&gProbeWatchKey)) {   // data-a-live: the probe's buffers at commit
   id<MTLBuffer> buffer=w[0];const NSUInteger offset=[w[1] unsignedLongValue];const uint8_t *c=(const uint8_t *)buffer.contents;
   // bb39: QuartzCore writes these buffers after binding them; what the GPU reads is what they hold at commit, so the
   // replay takes its bytes from here (1 KiB from the offset, in pieces).
   probeLog([NSString stringWithFormat:@"commit %@",w[2]],@"bytes",c && offset<buffer.length?probeHex(c+offset,buffer.length-offset,1024):@"(no contents)");
  }
  // Completion owns this reference""")
edit('NativeBridgeMetalFrontend.mm',
     """ @synchronized(gInFlight){[gInFlight removeObject:self];}
 if(NSSet *targets=objc_getAssociatedObject(self,&gProbeReadbackKey)) {""",
     """ @synchronized(gInFlight){[gInFlight removeObject:self];}
 if(NSArray *watch=objc_getAssociatedObject(self,&gProbeWatchKey)) {   // data-a-live: after completion, CPU and GPU views
  id<MTLCommandQueue> queue=(id<MTLCommandQueue>)self.queue;
  for(NSArray *w in watch) {
   id<MTLBuffer> buffer=w[0];const NSUInteger offset=[w[1] unsignedLongValue];NSString *label=w[2];
   const uint8_t *c=(const uint8_t *)buffer.contents;
   NSLog(@"pandora-metal watch %@ done, CPU: %@",label,c && offset<buffer.length?probeHex(c+offset,buffer.length-offset,64):@"(no contents)");
   dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY,0),^{
    const NSUInteger n=MIN((NSUInteger)64,buffer.length-offset);
    id<MTLBuffer> copy=[buffer.device newBufferWithLength:64 options:MTLResourceStorageModeShared];
    memset(copy.contents,0xa5,64);
    id<MTLCommandBuffer> cb=[queue commandBuffer];id<MTLBlitCommandEncoder> b=[cb blitCommandEncoder];
    [b copyFromBuffer:buffer sourceOffset:offset toBuffer:copy destinationOffset:0 size:n];
    [b endEncoding];[cb commit];[cb waitUntilCompleted];
    NSLog(@"pandora-metal watch %@ done, GPU: %@ (status %ld)",label,probeHex(copy.contents,n,64),(long)cb.status);
   });
  }
 }
 if(NSSet *targets=objc_getAssociatedObject(self,&gProbeReadbackKey)) {""")
edit('NativeBridgeMetalFrontend.mm',
     """   if(probe && bb==16 && d.width>=512) {""",
     """   if(probe && bb==16 && d.width>=512)   // data-a-live: QuartzCore's own descriptor of the image (bb39)
    NSLog(@"pandora-metal astc descriptor: usage %lu storage %lu cpu cache %lu hazard %lu resource options %lu swizzle %u/%u/%u/%u "
          "type %lu mips %lu samples %lu allow gpu opt %d | bytes %p length %lu",(unsigned long)d.usage,(unsigned long)d.storageMode,
          (unsigned long)d.cpuCacheMode,(unsigned long)d.hazardTrackingMode,(unsigned long)d.resourceOptions,(unsigned)d.swizzle.red,
          (unsigned)d.swizzle.green,(unsigned)d.swizzle.blue,(unsigned)d.swizzle.alpha,(unsigned long)d.textureType,
          (unsigned long)d.mipmapLevelCount,(unsigned long)d.sampleCount,(int)d.allowGPUOptimizedContents,
          bytes,(unsigned long)length);
   if(probe && bb==16 && d.width>=512) {""")

# ---- data-a-live: did the App Library draw make any fragment? (bb40) -------------------------------------------
# QuartzCore's fragment function forces the sampled alpha to 1, yet the App Library's target holds transparent black
# where the image goes: the draw makes no fragment, or one of its inputs scales the colour to nothing. With the probe,
# every render pass without its own visibility buffer gets one, and from the probed draw on each draw of that encoder
# counts its samples into its own slot (Metal's counting visibility results), logged at completion with the pipeline
# of each slot. The raster state (cull, winding, fill), the depth/stencil state and the index buffer at commit join
# the replay log.
edit('NativeBridgeMetalFrontend.mm',
     """static char gProbeWatchKey;""",
     """static char gProbeWatchKey,gProbeVisKey,gProbeVisLabelsKey,gProbeDepthKey;""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(d.visibilityResultBuffer) {
  if(![(id)d.visibilityResultBuffer isKindOfClass:[NBBuffer class]])""",
     """ NBBuffer *probeVisibility=nil;   // data-a-live: a visibility buffer of the probe's own for this pass
 if(pandoraAstcProbe() && !d.visibilityResultBuffer && gDeviceObject) {
  probeVisibility=(NBBuffer *)[(id<MTLDevice>)gDeviceObject newBufferWithLength:64*8 options:MTLResourceStorageModeShared];
  if(probeVisibility) {
   memset(probeVisibility.contents,0,64*8);
   [_resourceOwners addObject:probeVisibility];
   pass.visibility=probeVisibility.handle;
  }
 }
 if(d.visibilityResultBuffer) {
  if(![(id)d.visibilityResultBuffer isKindOfClass:[NBBuffer class]])""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(made && pandoraAstcProbe()) {   // data-a-live: the pass, for the probe's draw log
""",
     """ if(made && probeVisibility)objc_setAssociatedObject(made,&gProbeVisKey,probeVisibility,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 if(made && pandoraAstcProbe()) {   // data-a-live: the pass, for the probe's draw log
""")
# depth/stencil states keep their descriptor's description; the encoder remembers the bound one and the raster state
edit('NativeBridgeMetalFrontend.mm',
     """ NBDepthStencil *state=[NBDepthStencil new];state.handle=handle;return (id<MTLDepthStencilState>)state;""",
     """ NBDepthStencil *state=[NBDepthStencil new];state.handle=handle;
 if(pandoraAstcProbe())objc_setAssociatedObject(state,&gProbeDepthKey,[NSString stringWithFormat:@"depth compare %lu write %d stencil front %lu/%lu/%lu/%lu back %lu/%lu/%lu/%lu",
  (unsigned long)d.depthCompareFunction,(int)d.depthWriteEnabled,(unsigned long)d.frontFaceStencil.stencilCompareFunction,
  (unsigned long)d.frontFaceStencil.stencilFailureOperation,(unsigned long)d.frontFaceStencil.depthFailureOperation,
  (unsigned long)d.frontFaceStencil.depthStencilPassOperation,(unsigned long)d.backFaceStencil.stencilCompareFunction,
  (unsigned long)d.backFaceStencil.stencilFailureOperation,(unsigned long)d.backFaceStencil.depthFailureOperation,
  (unsigned long)d.backFaceStencil.depthStencilPassOperation],OBJC_ASSOCIATION_RETAIN_NONATOMIC);
 return (id<MTLDepthStencilState>)state;""")
edit('NativeBridgeMetalFrontend.mm',
     """ [self check:nmb_render_set_depth_stencil(self.handle,""",
     """ if(pandoraAstcProbe())probeBindings(self)[@"depthstencil"]=state?(objc_getAssociatedObject(state,&gProbeDepthKey)?:@"?"):@"none";
 [self check:nmb_render_set_depth_stencil(self.handle,""")
edit('NativeBridgeMetalFrontend.mm',
     """ [self check:nmb_render_set_raster(self.handle,""",
     """ if(pandoraAstcProbe())probeBindings(self)[@"raster"]=[NSString stringWithFormat:@"cull %llu winding %llu fill %llu",
  cull.unsignedLongLongValue,winding.unsignedLongLongValue,fill.unsignedLongLongValue];
 [self check:nmb_render_set_raster(self.handle,""")
# counting: the probed draw takes slot 0, every later draw of the encoder the next slot
edit('NativeBridgeMetalFrontend.mm',
     """ if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state
  static std::atomic<int> told{0};
  const int nth=told.fetch_add(1);
  if(nth<8) {
   const uint8_t *ic=""",
     """ if(pandoraAstcProbe() && objc_getAssociatedObject(self,&gProbeVisKey)) {   // data-a-live: count this draw's samples
  NSMutableArray *labels=objc_getAssociatedObject(self,&gProbeVisLabelsKey);
  if(probeBindings(self)[@"armed"] && !labels) {
   labels=[NSMutableArray array];objc_setAssociatedObject(self,&gProbeVisLabelsKey,labels,OBJC_ASSOCIATION_RETAIN_NONATOMIC);
   NSMutableArray *reads=objc_getAssociatedObject(self.commandBuffer,&gProbeVisKey);
   if(!reads){reads=[NSMutableArray array];objc_setAssociatedObject(self.commandBuffer,&gProbeVisKey,reads,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
   [reads addObject:@[objc_getAssociatedObject(self,&gProbeVisKey),labels]];
  }
  if(labels && labels.count<64) {
   [labels addObject:[NSString stringWithFormat:@"%@pipe %@",probeBindings(self)[@"armed"]?@"PROBED ":@"",probeBindings(self)[@"pipeid"]?:@"?"]];
   [(id<MTLRenderCommandEncoder>)self setVisibilityResultMode:MTLVisibilityResultModeCounting offset:(labels.count-1)*8];
  }
 }
 if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state
  static std::atomic<int> told{0};
  const int nth=told.fetch_add(1);
  if(nth<8) {
   const uint8_t *ic=""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state
  static std::atomic<int> told{0};
  const int nth=told.fetch_add(1);
  if(nth<8) {
   NSLog(@"pandora-metal replay d%d""",
     """ if(pandoraAstcProbe() && objc_getAssociatedObject(self,&gProbeVisKey)) {   // data-a-live: count this draw's samples
  NSMutableArray *labels=objc_getAssociatedObject(self,&gProbeVisLabelsKey);
  if(labels && labels.count<64) {
   [labels addObject:[NSString stringWithFormat:@"%@pipe %@",probeBindings(self)[@"armed"]?@"PROBED ":@"",probeBindings(self)[@"pipeid"]?:@"?"]];
   [(id<MTLRenderCommandEncoder>)self setVisibilityResultMode:MTLVisibilityResultModeCounting offset:(labels.count-1)*8];
  }
 }
 if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state
  static std::atomic<int> told{0};
  const int nth=told.fetch_add(1);
  if(nth<8) {
   NSLog(@"pandora-metal replay d%d""")
# the index buffer at commit, with the other watched buffers
edit('NativeBridgeMetalFrontend.mm',
     """   for(NSString *key in seen)if([key hasPrefix:@"~"] && [seen[key] count]==2) {""",
     """   if(b)[watch addObject:@[b,@(o),[NSString stringWithFormat:@"%d index+%lu",nth,(unsigned long)o]]];
   for(NSString *key in seen)if([key hasPrefix:@"~"] && [seen[key] count]==2) {""")
# at completion: the counts
edit('NativeBridgeMetalFrontend.mm',
     """ if(NSArray *watch=objc_getAssociatedObject(self,&gProbeWatchKey)) {   // data-a-live: after completion, CPU and GPU views""",
     """ static std::atomic<int> visTold{0};
 for(NSArray *read in objc_getAssociatedObject(self,&gProbeVisKey)) {   // data-a-live: samples counted per draw
  if(visTold.fetch_add(1)>=8)break;
  const uint64_t *counts=(const uint64_t *)[(id<MTLBuffer>)read[0] contents];NSArray *labels=read[1];
  for(NSUInteger i=0;i<labels.count && counts;i++)
   probeLog([NSString stringWithFormat:@"visibility %d slot %lu",visTold.load(),(unsigned long)i],
            [NSString stringWithFormat:@"%llu samples",(unsigned long long)counts[i]],labels[i]);
 }
 if(NSArray *watch=objc_getAssociatedObject(self,&gProbeWatchKey)) {   // data-a-live: after completion, CPU and GPU views""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(pandoraAstcProbe())probeBindings(self)[@"pipe"]=objc_getAssociatedObject(state,&gProbePipeKey)?:@"?";
""",
     """ if(pandoraAstcProbe())probeBindings(self)[@"pipe"]=objc_getAssociatedObject(state,&gProbePipeKey)?:@"?";
 if(pandoraAstcProbe())probeBindings(self)[@"pipeid"]=capId(state);
""")


# ---- data-a-live: the draws after the probed one too (bb41) --------------------------------------------------
# bb40: the App Library's ASTC draws make every fragment (1125x1046 samples each), and the next draws of the same
# pass (two of b2a8f, blending off, then b7854) cover the same area: they leave it transparent. Every draw after the
# probed one in its encoder now goes into the replay log with its whole state and its buffers at commit, so the pass
# can be replayed in order on the PC.
def edit_all(name, old, new, count):
    p = ROOT / name
    s = p.read_text()
    assert s.count(old) == count, (name, old[:70], s.count(old))
    p.write_text(s.replace(old, new))
    print('edited', name)


edit_all('NativeBridgeMetalFrontend.mm',
         """[probeBindings(self) removeObjectForKey:@"armed"];""",
         """[probeBindings(self) removeObjectForKey:@"armed"];probeBindings(self)[@"following"]=@YES;""", 2)
edit_all('NativeBridgeMetalFrontend.mm',
         """ if(pandoraAstcProbe() && probeBindings(self)[@"armed"]) {   // data-a-live: the probed draw's whole state""",
         """ if(pandoraAstcProbe() && (probeBindings(self)[@"armed"] || probeBindings(self)[@"following"])) {   // data-a-live: the probed draw's whole state, and the draws after it""", 2)
edit_all('NativeBridgeMetalFrontend.mm', """  if(nth<8) {""", """  if(nth<48) {""", 2)

# ---- data-a-live: buffer offsets moved after binding (bb42) --------------------------------------------------
# bb41 replayed on the PC: the draw after the App Library's ASTC draws (b2a8f) fills its quad with its vertex colour,
# blending off, as Apple's own IR says; which quad and colour depends on where its vertices are. QuartzCore moves the
# bound vertex buffer with setVertexBufferOffset between draws, which the replay log did not see. The probe's bindings
# and its commit-time watch now follow such moves.
edit('NativeBridgeMetalFrontend.mm',
     """- (void)offset:(NSUInteger)offset index:(NSUInteger)index stage:(uint32_t)stage {
""",
     """- (void)offset:(NSUInteger)offset index:(NSUInteger)index stage:(uint32_t)stage {
 if(pandoraAstcProbe()) {   // data-a-live: the binding moves; the probe's draw log and watch follow it
  NSMutableDictionary *seen=probeBindings(self);
  NSString *key=[NSString stringWithFormat:@"~%sbuffer%lu",probeStage(stage),(unsigned long)index];
  NSArray *bound=seen[key];
  if(bound.count==2)seen[key]=@[bound[0],@(offset)];
  seen[[NSString stringWithFormat:@"%sbufferoffset%lu",probeStage(stage),(unsigned long)index]]=@(offset);
 }
""")

# ---- data-a-live: 'b3a8' surfaces whose planes report no row bytes (bb43) -------------------------------------
# bb43: QuartzCore's own App Library draws render right on the phone (mtltest part 9). What stays empty is a layer
# drawn from a full-screen two-plane extended-range surface ('b3a8', RGB10A8_2P_XR10): the frontend refused it every
# time (step 3) because IOSurfaceGetBytesPerRowOfPlane answers 0 for it here. Its allocation is exactly two linear
# planes, rows aligned to 64 bytes, one after the other, rounded to 4 KiB: 1125x2436 -> 4544 * 2436 + 1152 * 2436 =
# 13875456 -> 13877248, 1065x282 -> 1519616, as logged. Those planes are now computed when the surface gives none.
edit('NativeBridgeMetalFrontend.mm',
     """static NBTexture *textureOnSurface(NBBuffer *buffer,""",
     """// data-a-live: a 'b3a8' surface's planes when it reports none (bb43): {offset0, row0, offset1, row1}, or false.
static bool pandoraB3a8Planes(IOSurfaceRef surface,size_t allocation,size_t planes[4]) {
 if(!surface || IOSurfaceGetPixelFormat(surface)!=0x62336138 || IOSurfaceGetPlaneCount(surface)!=2)return false;
 if(IOSurfaceGetBytesPerRowOfPlane(surface,0) && IOSurfaceGetBytesPerRowOfPlane(surface,1))return false;
 const size_t w=IOSurfaceGetWidth(surface),h=IOSurfaceGetHeight(surface);
 const size_t row0=(w*4+63)&~(size_t)63,row1=(w+63)&~(size_t)63;
 if(!w || !h || row0*h+row1*h>allocation)return false;
 planes[0]=0;planes[1]=row0;planes[2]=row0*h;planes[3]=row1;
 static std::atomic<int> told{0};
 if(told.fetch_add(1)<8)NSLog(@"pandora-metal b3a8 %lux%lu: planes computed (row %lu at 0, row %lu at %lu; %lu of %lu bytes)",(unsigned long)w,
  (unsigned long)h,(unsigned long)row0,(unsigned long)row1,(unsigned long)(row0*h),(unsigned long)(row0*h+row1*h),(unsigned long)allocation);
 return true;
}
static NBTexture *textureOnSurface(NBBuffer *buffer,""")
edit('NativeBridgeMetalFrontend.mm',
     """ const uint64_t base=(uintptr_t)IOSurfaceGetBaseAddress(surface),chroma=(uintptr_t)IOSurfaceGetBaseAddressOfPlane(surface,1);
 NmbTexture *handle=nmb_texture_new_on_surface(buffer.handle,&descriptor,offset,row,IOSurfaceGetPixelFormat(surface),plane,
  IOSurfaceGetWidth(surface),IOSurfaceGetHeight(surface),chroma>=base?chroma-base:0,IOSurfaceGetBytesPerRowOfPlane(surface,1));""",
     """ const uint64_t base=(uintptr_t)IOSurfaceGetBaseAddress(surface),chroma=(uintptr_t)IOSurfaceGetBaseAddressOfPlane(surface,1);
 uint64_t chromaOffset=chroma>=base?chroma-base:0,chromaRow=IOSurfaceGetBytesPerRowOfPlane(surface,1);
 size_t computed[4];   // data-a-live: a 'b3a8' surface without plane rows (bb43)
 if(!chromaRow && pandoraB3a8Planes(surface,IOSurfaceGetAllocSize(surface),computed)){chromaOffset=computed[2];chromaRow=computed[3];}
 NmbTexture *handle=nmb_texture_new_on_surface(buffer.handle,&descriptor,offset,row,IOSurfaceGetPixelFormat(surface),plane,
  IOSurfaceGetWidth(surface),IOSurfaceGetHeight(surface),chromaOffset,chromaRow);""")
edit('NativeBridgeMetalFrontend.mm',
     """ const uintptr_t start=planes?(uintptr_t)IOSurfaceGetBaseAddressOfPlane(surface,plane):(uintptr_t)base;
 const size_t row=planes?IOSurfaceGetBytesPerRowOfPlane(surface,plane):IOSurfaceGetBytesPerRow(surface);
""",
     """ uintptr_t start=planes?(uintptr_t)IOSurfaceGetBaseAddressOfPlane(surface,plane):(uintptr_t)base;
 size_t row=planes?IOSurfaceGetBytesPerRowOfPlane(surface,plane):IOSurfaceGetBytesPerRow(surface);
 size_t computed[4];   // data-a-live: a 'b3a8' surface without plane rows (bb43)
 if(!row && plane<2 && pandoraB3a8Planes(surface,allocation,computed)){start=(uintptr_t)base+computed[plane*2];row=computed[plane*2+1];}
""")

# ---- data-a-live: where the bridge's CPU time goes, and how Turnip draws (2026-10-25, round 1) -----------------
# While the screen moves backboardd uses up to two thirds of a core, its render thread almost half (app1). Every call
# into the bridge (metal2vulkan's runtime and Turnip below it, on the caller's thread) is now timed: per bridge
# function its calls and time, and the time inside the bridge at all (a call inside a call counted once; the
# blocking waits apart). backboardd logs it every 10 s for 10 minutes as "pandora-metal B", the busiest functions
# first, with Turnip's render pass counts (GMEM or sysmem and Turnip's reason, tools/turnip/pandora_rpstats.py).
# Counting only: no call changes. PandoraBridgeTiming.h is generated from the bridge's header: one macro per
# function wraps each call in the frontend in a timed lambda.
import re

names = sorted(set(re.findall(r'^[A-Za-z_][A-Za-z0-9_ *]*?\b(nmb_[a-z0-9_]+)\(',
                              (ROOT / 'bridge/NativeMetalObjects.h').read_text(), re.M)))
assert len(names) > 60, len(names)
front = (ROOT / 'NativeBridgeMetalFrontend.mm').read_text()
for n in names:
    assert not re.search(r'^[A-Za-z].*\b%s\([^;]*\)\s*\{' % n, front, re.M), ('defined in the frontend', n)
waits = [n for n in names if '_wait' in n]
(ROOT / 'PandoraBridgeTiming.h').write_text(
    '// Generated by tools/metal/adapt_ios16.py (data-a-live): every bridge call in the frontend counted, 1 in 16 timed.\n'
    '#pragma once\n#include <atomic>\n#include <time.h>\n'
    'enum {' + ','.join('PANDORA_BT_' + n for n in names) + ',PANDORA_BT_COUNT};\n'
    'static const char *const gPandoraBridgeNames[PANDORA_BT_COUNT]={' + ','.join('"%s"' % n for n in names) + '};\n'
    'static const bool gPandoraBridgeWait[PANDORA_BT_COUNT]={' + ','.join('true' if n in waits else 'false' for n in names) + '};\n'
    'struct PandoraBridgeSlot {std::atomic<uint64_t> calls{0},ns{0};};\n'
    'static PandoraBridgeSlot gPandoraBridge[PANDORA_BT_COUNT];\n'
    'static std::atomic<uint64_t> gPandoraBridgeOuter{0},gPandoraBridgeWaits{0};\n'
    '// Every call counted; one in PANDORA_BT_EVERY timed, picked at random (xorshift32, so no pattern of calls always\n'
    '// lands on the same one) and counted PANDORA_BT_EVERY times; the blocking waits, few and long, all timed.\n'
    'enum {PANDORA_BT_EVERY=16};\n'
    'struct PandoraBridgeThread {unsigned depth;uint32_t dice;};\n'
    'static thread_local PandoraBridgeThread gPandoraBridgeThread{0,0x9e3779b9u};\n'
    'template<class F> static inline decltype(auto) pandoraTimed(unsigned slot,F &&f) {\n'
    ' PandoraBridgeThread &me=gPandoraBridgeThread;\n'
    ' gPandoraBridge[slot].calls.fetch_add(1,std::memory_order_relaxed);\n'
    ' uint32_t x=me.dice;x^=x<<13;x^=x>>17;x^=x<<5;me.dice=x;\n'
    ' const bool wait=gPandoraBridgeWait[slot];\n'
    ' struct End {PandoraBridgeThread &me;unsigned slot;bool wait;uint64_t t0;~End(){const unsigned depth=--me.depth;if(!t0)return;\n'
    '  const uint64_t d=(clock_gettime_nsec_np(CLOCK_UPTIME_RAW)-t0)*(wait?1:PANDORA_BT_EVERY);\n'
    '  gPandoraBridge[slot].ns.fetch_add(d,std::memory_order_relaxed);\n'
    '  if(depth==0)(wait?gPandoraBridgeWaits:gPandoraBridgeOuter).fetch_add(d,std::memory_order_relaxed);}};\n'
    ' ++me.depth;\n'
    ' End end{me,slot,wait,wait || (x&(PANDORA_BT_EVERY-1))==0?clock_gettime_nsec_np(CLOCK_UPTIME_RAW):0};\n'
    ' return f();\n'
    '}\n'
    + ''.join('#define %s(...) pandoraTimed(PANDORA_BT_%s,[&]() -> decltype(auto) {return %s(__VA_ARGS__);})\n' % (n, n, n)
              for n in names))
print('wrote PandoraBridgeTiming.h,', len(names), 'functions,', len(waits), 'waits')
edit('NativeBridgeMetalFrontend.mm',
     """#include "NativeBridgeMetalFrontend.h"
""",
     """#include "NativeBridgeMetalFrontend.h"
#include "PandoraBridgeTiming.h"   // data-a-live: every bridge call counted, 1 in 16 timed (tools/metal/adapt_ios16.py)
""")
p = ROOT / 'NativeBridgeMetalFrontend.mm'
p.write_text(p.read_text() + r'''
// data-a-live: the bridge's time since the last call, as text (tools/metal/adapt_ios16.py, 2026-10-25): the share of
// one CPU inside the bridge and in its blocking waits, then the busiest functions; then Turnip's render pass counts.
extern "C" size_t native_bridge_metal_pandora_timing(char *out,size_t size) {
 static uint64_t last,lastOuter,lastWaits,shownCalls[PANDORA_BT_COUNT],shownNs[PANDORA_BT_COUNT];
 const uint64_t now=clock_gettime_nsec_np(CLOCK_UPTIME_RAW),span=last?now-last:0;
 last=now;
 const uint64_t outer=gPandoraBridgeOuter.load(),waits=gPandoraBridgeWaits.load();
 const uint64_t dOuter=outer-lastOuter,dWaits=waits-lastWaits;
 lastOuter=outer;lastWaits=waits;
 uint64_t calls[PANDORA_BT_COUNT],ns[PANDORA_BT_COUNT];
 unsigned order[PANDORA_BT_COUNT];
 for(unsigned i=0;i<PANDORA_BT_COUNT;i++){
  const uint64_t c=gPandoraBridge[i].calls.load(),t=gPandoraBridge[i].ns.load();
  calls[i]=c-shownCalls[i];ns[i]=t-shownNs[i];shownCalls[i]=c;shownNs[i]=t;order[i]=i;
 }
 std::sort(order,order+PANDORA_BT_COUNT,[&](unsigned a,unsigned b){return ns[a]>ns[b];});
 int at=snprintf(out,size,"bridge %llu.%llu%% of one CPU over %llu ms, waits %llu ms (1 call in 16 timed)\n",
  (unsigned long long)(span?dOuter*100/span:0),(unsigned long long)(span?dOuter*1000/span%10:0),
  (unsigned long long)(span/1000000),(unsigned long long)(dWaits/1000000));
 for(unsigned k=0;k<14 && at>0 && (size_t)at<size && ns[order[k]];k++){
  const unsigned i=order[k];
  at+=snprintf(out+at,size-at,"  %s: %llu calls, %llu us (%llu ns each)\n",gPandoraBridgeNames[i],(unsigned long long)calls[i],
   (unsigned long long)(ns[i]/1000),(unsigned long long)(calls[i]?ns[i]/calls[i]:0));
 }
 static size_t (*stats)(char *,size_t);
 static bool looked;
 if(!looked){
  looked=true;
  for(const char *path:{"/usr/lib/libvulkan_freedreno.dylib",(const char *)nullptr}){
   void *image=path?dlopen(path,RTLD_NOLOAD|RTLD_LAZY):RTLD_DEFAULT;
   if(image && (stats=(size_t (*)(char *,size_t))dlsym(image,"tu_pandora_render_stats")))break;
  }
 }
 if(at>0 && (size_t)at<size){
  if(stats){at+=snprintf(out+at,size-at,"turnip: ");if((size_t)at<size)at+=(int)stats(out+at,size-at);}
  else at+=snprintf(out+at,size-at,"turnip: no render pass counts (tu_pandora_render_stats not found)");
 }
 return at>0?std::min((size_t)at,size?size-1:0):0;
}
''')
print('edited NativeBridgeMetalFrontend.mm (timing report)')
edit('NativeAdrenoMetalPlugin.mm',
     """#include <sys/sysctl.h>
""",
     """#include <sys/sysctl.h>
extern "C" size_t native_bridge_metal_pandora_timing(char *out, size_t size);   // data-a-live (NativeBridgeMetalFrontend.mm)
extern "C" size_t native_bridge_metal_pandora_work(char *out, size_t size);     // data-a-live (NativeBridgeMetalFrontend.mm)
""")
edit('NativeAdrenoMetalPlugin.mm',
     """        NSLog(@"pandora-metal: backboardd has a device; CAEnableDeepFramebuffer %@", (__bridge id)deep);
""",
     """        NSLog(@"pandora-metal: backboardd has a device; CAEnableDeepFramebuffer %@", (__bridge id)deep);
        pthread_t timing;   // data-a-live: the bridge's time and Turnip's render passes, every 10 s for 10 minutes
        if (pthread_create(&timing, nullptr, [](void *) -> void * {
                static char text[4096], work[8192];
                native_bridge_metal_pandora_timing(text, sizeof text);   // starts the first interval
                for (unsigned round = 0; round < 60; ++round) {
                    sleep(10);
                    native_bridge_metal_pandora_timing(text, sizeof text);
                    for (char *line = text, *end; *line; line = end) {
                        end = strchr(line, '\\n');
                        if (end) *end++ = 0; else end = line + strlen(line);
                        NSLog(@"pandora-metal B%u %s", round, line);
                    }
                    // The runtime's work counts that changed (round 0: since it started): shadow copies and the like.
                    native_bridge_metal_pandora_work(work, sizeof work);
                    for (char *line = work, *end; *line; line = end) {
                        end = strchr(line, '\\n');
                        if (end) *end++ = 0; else end = line + strlen(line);
                        NSLog(@"pandora-metal W%u %s", round, line);
                    }
                }
                return nullptr;
            }, nullptr) == 0) pthread_detach(timing);
""")

# ---- device answers asked once per (selector, argument) ------------------------------------------------------------
# r1c: QuartzCore asks the device about 3100 questions with an argument every 10 s while the screen moves, 140 us each.
# Upstream 31867dc ("keep each device answer that takes an argument") keeps them the same way; nothing to edit here.

# ---- data-a-live: the runtime's work counts in the 10-second report (2026-10-25, round 2) --------------------------
# r2n's call stacks put a quarter of a pass's end in shadow copies (EncoderCore::shadow_copies, Turnip's r2d blits): a
# texture over a buffer or an IOSurface plane that no linear image aliases is an image of its own whose texels the
# encoders copy in and back (metal2vulkan-runtime shadow.rs). Which of QuartzCore's surfaces take that path, and what it
# moves a frame, is the runtime's own count (Device::work_counts: shadowed_textures, shadow_copies,
# shadow_texels_copied_back, among queue submissions, pipelines made and the rest): the report prints every count that
# changed since the last ("pandora-metal W<round> <name> +<change> <now>"). Counting only, as the bridge timing.
edit('bridge/NativeMetalObjects.h',
     """size_t nmb_device_family_waivers(const NmbDevice *, char *out, size_t size); /* the target families' rows not met, one family per line */
""",
     """size_t nmb_device_family_waivers(const NmbDevice *, char *out, size_t size); /* the target families' rows not met, one family per line */
size_t nmb_device_work_counts(const NmbDevice *, char *out, size_t size); /* data-a-live: the work counts that changed since the last call, "name +change now" a line */
""")
p = ROOT / 'bridge/NativeMetalObjects.rs'
p.write_text(p.read_text() + r'''
/// data-a-live: the device's work counts (`Device::work_counts`) that changed since the last call, a line each, `name
/// +change now`, into `out` (NUL-terminated); the length the whole text needs. The first call lists every count above 0.
/// For the plugin's 10-second report (tools/metal/adapt_ios16.py).
#[unsafe(no_mangle)]
pub extern "C" fn nmb_device_work_counts(device: *const NmbDevice, out: *mut c_char, size: usize) -> usize {
    static LAST: std::sync::Mutex<Vec<(String, u64)>> = std::sync::Mutex::new(Vec::new());
    guard(0, || {
        let counts = format!("{:#?}", get(device)?.0.work_counts());
        let mut last = LAST.lock().map_err(|_| "work counts lock poisoned".to_string())?;
        let mut text = String::new();
        for line in counts.lines() {
            let Some((name, value)) = line.trim().trim_end_matches(',').split_once(": ") else { continue };
            let Ok(now) = value.parse::<u64>() else { continue };
            let before = match last.iter_mut().find(|(known, _)| known == name) {
                Some((_, held)) => std::mem::replace(held, now),
                None => {
                    last.push((name.to_owned(), now));
                    0
                }
            };
            if now != before {
                text += &format!("{name} {:+} {now}\n", i128::from(now) - i128::from(before));
            }
        }
        Ok(copy_out(&text, out, size))
    })
}
''')
# ---- data-a-live: padded targets and rebinds ---------------------------------------------------------------
# Keep the maintained local m2v 0003/0009 representation: writable padded targets
# use a separate policy and the measured 16-by-4 Turnip store alignment. The
# overlapping AdrenoMetal 0008/0016 patches are not part of this patch stack.
edit('bridge/NativeMetalObjects.rs',
     """        // A texture over a buffer whose rows lie apart is an image over the bytes, rendered or written too: the window server's padded
        // framebuffers (3048x2032 BGRA8 at 12288-byte rows, int-251) were otherwise copied in and out of a shadow image every pass.
        options.policy.padded_reads = metal2vulkan::device::PaddedReadsPolicy::AliasWrites;
""",
     """        // The maintained local patches separate writable padded targets from padded reads.
        // Preserve their measured 16-by-4 Turnip store alignment when aliasing targets.
        if !pandora_boot_arg_is("pandora_m2v_padded", "shadow") {
            options.policy.padded_targets = metal2vulkan::device::PaddedTargetsPolicy::Alias;
            options.render_store_alignment = Some([16, 4]);
        }
        // data-a-live: this frontend never rebinds a no-copy buffer (NativeMetalObjects.h), so the
        // encoders need not log their calls for one; pandora_m2v_rebinds=allow keeps the logs.
        options.refuse_rebinds = !pandora_boot_arg_is("pandora_m2v_rebinds", "allow");
""")
p = ROOT / 'bridge/NativeMetalObjects.rs'
p.write_text(p.read_text() + r'''
/// data-a-live: whether the kernel's boot-args (`kern.bootargs`) hold `name=value`.
fn pandora_boot_arg_is(name: &str, value: &str) -> bool {
    let mut args = [0u8; 1024];
    let mut len = args.len();
    // SAFETY: `args` holds `len` bytes; the name is a C string.
    let read = unsafe {
        libc::sysctlbyname(c"kern.bootargs".as_ptr(), args.as_mut_ptr().cast(), &mut len, std::ptr::null_mut(), 0)
    };
    if read != 0 {
        return false;
    }
    let args = String::from_utf8_lossy(&args[..len.min(args.len())]);
    let wanted = format!("{name}={value}");
    args.split_whitespace().any(|arg| arg.trim_end_matches('\0') == wanted)
}
''')
# A texture made on an IOSurface counts as a use of the surface (IOSurfaceIncrementUseCount) until it goes. Settings showed
# one row's words in another row for a second or two (shot1, shot2: 完成 and 音符 drawn as 圆圈, the row below), with
# every imported surface out of the CPU caches before each submission too (shot2, pandora_gpu_flushall=1). QuartzCore
# reuses a backing-store buffer when IOSurfaceIsInUse says no (buffer_is_unused, ios-fuxi docs-lanes/gpu-scanout.md);
# the kernel publishes the clients' use counts there (0217), but nothing counted the render server's textures, so an
# app could draw new words into a surface backboardd still showed. ios-fuxi's frontend counts its imports the same way.
# Views hold their parent texture and count nothing of their own. Only the render server counts, as AdrenoMetal aff537e
# does: a client's textures over its own drawables would keep them in use while CAMetalLayer holds them for reuse.
edit('NativeBridgeMetalFrontend.mm',
     """@property(nonatomic) IOSurfaceRef surface;
""",
     """@property(nonatomic) IOSurfaceRef surface;
@property(nonatomic) BOOL surfaceUse;   // data-a-live: holds an IOSurfaceIncrementUseCount
""")
edit('NativeBridgeMetalFrontend.mm',
     """- (void)dealloc {nmb_texture_release(_handle);if(_surface)CFRelease(_surface);}""",
     """- (void)dealloc {nmb_texture_release(_handle);if(_surface){if(_surfaceUse)IOSurfaceDecrementUseCount(_surface);CFRelease(_surface);}}""")
edit('NativeBridgeMetalFrontend.mm',
     """gSurfaceTextures++;texture.surface=(IOSurfaceRef)CFRetain(surface);return (id<MTLTexture>)texture;""",
     """gSurfaceTextures++;texture.surface=(IOSurfaceRef)CFRetain(surface);
 static const bool countsUse=!strcmp(getprogname(),"backboardd");
 if(countsUse){IOSurfaceIncrementUseCount(surface);texture.surfaceUse=YES;}
 return (id<MTLTexture>)texture;""")

# AegirPoster (the Astronomy poster) aborted at every launch with CAMetalLayerInvalid "unsupported extended range format"
# (post1): QuartzCore's layer pixel-format check asks supportsExtendedXR10Formats for its BGR10_XR_sRGB layer, and the
# frontend answers NO in every process. The NO stays for the render server (an XR10 answer there picks a 64-bit
# framebuffer this scanout cannot show, int-089) and PosterBoard; poster extensions get the device's answer, as
# AdrenoMetal aff537e and myron's stage 115 (AEGIRPOSTER-SOLUTION.md 3.5) do.
edit('NativeBridgeMetalFrontend.mm',
     """   if(!strcmp(name,"supportsExtendedXR10Formats"))value=0;""",
     """   if(!strcmp(name,"supportsExtendedXR10Formats") && (!strstr(getprogname(),"Poster") || !strcmp(getprogname(),"PosterBoard")))value=0;
   if(!strcmp(name,"supportsExtendedXR10Formats"))NSLog(@"pandora-metal: supportsExtendedXR10Formats %llu",(unsigned long long)value);""")

# PridePosterExtension aborted (post29, every process given a device) with "-[NBComputeEncoder globalTraceObjectID]:
# unrecognized selector": Apple's objects (encoders, command buffers, resources) answer the private tracing id that
# Metal's GPU-trace and capture paths read. Every bridge object answers 0, "no trace object".
edit('NativeBridgeMetalFrontend.mm',
     """@implementation NBObject
- (id<MTLDevice>)device {return gDeviceObject;}
""",
     """@implementation NBObject
- (id<MTLDevice>)device {return gDeviceObject;}
- (uint64_t)globalTraceObjectID {return 0;}
// post30: PridePosterExtension then stopped on -[NBCommandBuffer userDictionary] (Apple's command buffers keep a
// private mutable dictionary for their clients); each bridge object keeps one, made on first use.
- (NSMutableDictionary *)userDictionary {
 @synchronized(self) {
  NSMutableDictionary *d=objc_getAssociatedObject(self,@selector(userDictionary));
  if(!d){d=[NSMutableDictionary dictionary];objc_setAssociatedObject(self,@selector(userDictionary),d,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}
  return d;
 }
}
""")

# ---- data-a-live: Metal conformance step 1 (2026-10-30) ----------------------------------------------------------
# The goal is a device that answers like a real iPhone X to every client, third-party apps included
# (tools/metal/conformance.py lists the methods the bridge's objects lack against the SDK and the A11 driver).
# 1. Every bridge class adopts its public Metal protocol, as Apple's driver classes do. Only the device did, so
#    conformsToProtocol: and Swift's casts to a Metal protocol (`as? MTLTexture`, class_conformsToProtocol) answered NO
#    for every other object. Adoption only answers that question; each method is still looked up on its own.
# 2. What clients ask the objects and get NO for is logged, each (question, class, name) once per process, at most 400:
#    respondsToSelector: and instancesRespondToSelector:, methodForSelector: of a missing method (RenderBox caches IMPs
#    that way), conformsToProtocol:. A framework that asks first and takes another path on NO leaves no other trace.
edit('NativeBridgeMetalFrontend.mm',
     """#include <unordered_map>
""",
     """#include <unordered_map>
#include <unordered_set>
#include <atomic>
""")
edit('NativeBridgeMetalFrontend.mm',
     """@implementation NBObject
""",
     """// data-a-live: a question a client asked a bridge object and got NO for (respondsToSelector: and the like), logged once per
// (question, class, name) into the unified log, at most 400 a process (tools/metal/adapt_ios16.py, conformance step 1).
static void pandoraAsked(unsigned how,Class cls,const void *what,const char *name) {
 static const char *const hows[]={"responds","conforms","methodFor"};
 static std::mutex lock;
 static std::unordered_set<uint64_t> seen;
 static std::atomic<bool> full{false};
 if(full.load(std::memory_order_relaxed))return;
 const uint64_t key=((uint64_t)(uintptr_t)cls*1000003u)^((uint64_t)(uintptr_t)what<<2)^how;
 {std::lock_guard<std::mutex> hold(lock);
  if(!seen.insert(key).second)return;
  if(seen.size()>=400)full=true;}
 NSLog(@"pandora-metal asked: %s %s %s -> NO",hows[how],class_getName(cls),name);
}
static BOOL pandoraConforms(Class cls,Protocol *protocol) {
 for(Class c=cls;c;c=class_getSuperclass(c))if(class_conformsToProtocol(c,protocol))return YES;
 return NO;
}
#define PANDORA_ASKED_METHODS \\
- (BOOL)respondsToSelector:(SEL)sel { \\
 if(class_respondsToSelector(object_getClass(self),sel))return YES; \\
 pandoraAsked(0,object_getClass(self),sel,sel_getName(sel));return NO; \\
} \\
- (BOOL)conformsToProtocol:(Protocol *)protocol { \\
 if(pandoraConforms(object_getClass(self),protocol))return YES; \\
 pandoraAsked(1,object_getClass(self),(__bridge const void *)protocol,protocol_getName(protocol));return NO; \\
} \\
- (IMP)methodForSelector:(SEL)sel { \\
 if(!class_respondsToSelector(object_getClass(self),sel))pandoraAsked(2,object_getClass(self),sel,sel_getName(sel)); \\
 return class_getMethodImplementation(object_getClass(self),sel); \\
}
@implementation NBObject
PANDORA_ASKED_METHODS
+ (BOOL)instancesRespondToSelector:(SEL)sel {
 if(class_respondsToSelector(self,sel))return YES;
 pandoraAsked(0,self,sel,sel_getName(sel));return NO;
}
""")
edit('NativeBridgeMetalFrontend.mm',
     """@implementation NBDeviceMethods
- (NSString *)name {return gName;}
""",
     """@implementation NBDeviceMethods
PANDORA_ASKED_METHODS   // data-a-live: copied onto the device class with the rest (native_bridge_metal_open)
- (NSString *)name {return gName;}
""")
edit('NativeBridgeMetalFrontend.mm',
     """ if(Protocol *spi=objc_getProtocol("MTLDeviceSPI"))class_addProtocol(cls,spi);
""",
     """ if(Protocol *spi=objc_getProtocol("MTLDeviceSPI"))class_addProtocol(cls,spi);
 // data-a-live: every other bridge class adopts its public Metal protocol too (tools/metal/adapt_ios16.py, conformance step 1).
 const struct {const char *cls;Protocol *protocol;} adopted[]={
  {"NBQueue",@protocol(MTLCommandQueue)},{"NBCommandBuffer",@protocol(MTLCommandBuffer)},
  {"NBRenderEncoder",@protocol(MTLRenderCommandEncoder)},{"NBComputeEncoder",@protocol(MTLComputeCommandEncoder)},
  {"NBBlitEncoder",@protocol(MTLBlitCommandEncoder)},{"NBTexture",@protocol(MTLTexture)},{"NBBuffer",@protocol(MTLBuffer)},
  {"NBHeap",@protocol(MTLHeap)},{"NBLibrary",@protocol(MTLLibrary)},{"NBFunction",@protocol(MTLFunction)},
  {"NBRenderPipeline",@protocol(MTLRenderPipelineState)},{"NBComputePipeline",@protocol(MTLComputePipelineState)},
  {"NBSampler",@protocol(MTLSamplerState)},{"NBDepthStencil",@protocol(MTLDepthStencilState)},{"NBEvent",@protocol(MTLEvent)},
  {"NBSharedEvent",@protocol(MTLSharedEvent)},{"NBFence",@protocol(MTLFence)},{"NBBinaryArchive",@protocol(MTLBinaryArchive)}};
 for(const auto &a:adopted)if(Class c=objc_getClass(a.cls))class_addProtocol(c,a.protocol);
""")

p = ROOT / 'NativeBridgeMetalFrontend.mm'
p.write_text(p.read_text() + r'''
// data-a-live: the runtime's work counts that changed since the last call (NativeMetalObjects.rs nmb_device_work_counts).
extern "C" size_t native_bridge_metal_pandora_work(char *out,size_t size) {
 if(!gDevice){if(size)*out=0;return 0;}
 return nmb_device_work_counts(gDevice,out,size);
}
''')
print('added the work counts to the report')

# Every admitted client needs the iOS ABI adapter before its first native device.
# Wallpaper extensions do not inherit SpringBoard/backboardd's injection environment.
edit('NativeAdrenoMetalPlugin.mm',
     """    char turnip[1024];
    if (!turnip::libraryPath""",
     """    // Every native client needs the iOS private-ABI adapter, including ImageIO's
    // texture layouts and Core Image stitching. Extension hosts do not inherit
    // the daemon environment that injects it into SpringBoard/backboardd.
    static dispatch_once_t compatibilityOnce;
    static void *compatibility;
    dispatch_once(&compatibilityOnce, ^{
        compatibility = dlopen("/System/Library/Extensions/NativeAdrenoMetal.bundle/NezhaStatusBar", RTLD_NOW | RTLD_LOCAL);
        if (compatibility) NSLog(@"NEZHA Metal: client compatibility ready for %s", getprogname());
    });
    if (!compatibility) {
        const char *detail = dlerror();
        char error[256];
        snprintf(error, sizeof error, "%s", detail ? detail : "unavailable");
        NSLog(@"NEZHA Metal: client compatibility load failed: %s", error);
        clientFailure(port, "no-ios16-shim", error);
        return nil;
    }
    if (!strcmp(getprogname(), "AegirPoster")) {
        gClientPort = port;
        static dispatch_once_t reporterOnce;
        dispatch_once(&reporterOnce, ^{
            if (auto setReporter = (void (*)(void (*)(const char *)))dlsym(compatibility, "nezha_wallpaper_location_set_reporter"))
                setReporter([](const char *text) {
                    if (Gpu *gpu = makeGpu(gClientPort)) { streamText(*gpu, text); delete gpu; }
                });
        });
    }
    char turnip[1024];
    if (!turnip::libraryPath""")
edit('NativeAdrenoMetalPlugin.mm',
     """        streamText(*gpu, text);
    }
}
static id clientDevice""",
     """        streamText(*gpu, text);
        delete gpu;
    }
}
static id clientDevice""")

# Bounded evidence that the specialized Core Image container reached the loader.
edit('bridge/NativeMetalObjects.rs',
     r"""        let names = library.function_names();
        Ok(boxed(NmbLibrary(library, names)))""",
     r"""        let names = library.function_names();
        // Bounded loading evidence; kernel execution and stitching remain separate capabilities.
        if !air && crate::air_slice(bytes).is_some_and(|data| data.starts_with(b"MTLB") && data.get(10) == Some(&1)) {
            use std::sync::atomic::{AtomicU32, Ordering};
            static REPORTED: AtomicU32 = AtomicU32::new(0);
            if REPORTED.fetch_add(1, Ordering::Relaxed) < 8 {
                let message = format!("NEZHA Metal: Core Image kernel library loaded, bytes {}, entry points {}\0", length, names.len());
                // SAFETY: the diagnostic and format are NUL-terminated for this call.
                unsafe { syslog(5, c"%s".as_ptr(), message.as_ptr().cast::<c_char>()) };
            }
        }
        Ok(boxed(NmbLibrary(library, names)))""")

# MPS resource sizing uses the runtime's existing creation layout.
edit('bridge/NativeMetalObjects.rs',
     r'''// ---- textures ----
''',
     r'''/// Heap sizing uses the same runtime descriptor and layout path as resource creation.
/// A refused descriptor leaves both output words zero and records the actual error.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_device_heap_texture_size_and_align(device: *const NmbDevice, descriptor: *const NmbTextureDescriptor, out: *mut u64) -> bool {
    guard(false, || {
        // SAFETY: the C caller supplies two writable u64 words; reject a null output.
        let out = unsafe { out.cast::<[u64; 2]>().as_mut() }.ok_or("heap size output is null")?;
        *out = [0, 0];
        let answer = get(device)?.0.heap_texture_size_and_align(&texture_descriptor(descriptor)?).map_err(e("heap texture size"))?;
        *out = [answer.size, answer.align];
        Ok(true)
    })
}

/// The buffer counterpart of the texture heap-size query; options stay checked.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_device_heap_buffer_size_and_align(device: *const NmbDevice, length: u64, options_word: u64, out: *mut u64) -> bool {
    guard(false, || {
        // SAFETY: as in the texture query.
        let out = unsafe { out.cast::<[u64; 2]>().as_mut() }.ok_or("heap size output is null")?;
        *out = [0, 0];
        let answer = get(device)?.0.heap_buffer_size_and_align(length, options(options_word)?);
        *out = [answer.size, answer.align];
        Ok(true)
    })
}

// ---- textures ----
''')

# MPS resource sizing uses the runtime's existing creation layout.
edit('NativeBridgeMetalFrontend.mm',
     r'''- (id)newResourceGroupFromResources:(const id __unsafe_unretained [])resources count:(NSUInteger)count {
''',
     r'''// MPS asks these before allocating temporary textures/buffers. Query the exact
// runtime layout used by newHeap/newTexture instead of inventing a byte estimate.
- (MTLSizeAndAlign)heapTextureSizeAndAlignWithDescriptor:(MTLTextureDescriptor *)d {
 NmbTextureDescriptor descriptor;uint64_t answer[2]={0,0};
 if(!textureDescriptor(d,&descriptor))return (MTLSizeAndAlign){0,0};
 if(!nmb_device_heap_texture_size_and_align(gDevice,&descriptor,answer))
  NBLOG(@"native-metal: heap texture size (fmt %lu %lux%lu, %lu levels) refused: %@",(unsigned long)d.pixelFormat,(unsigned long)d.width,(unsigned long)d.height,(unsigned long)d.mipmapLevelCount,lastError());
 else {
  static std::atomic<unsigned> reports{0};
  if(reports.fetch_add(1,std::memory_order_relaxed)<4)
   NSLog(@"NEZHA Metal: heap texture size %lux%lu format=%lu size=%llu align=%llu",(unsigned long)d.width,(unsigned long)d.height,(unsigned long)d.pixelFormat,(unsigned long long)answer[0],(unsigned long long)answer[1]);
 }
 return (MTLSizeAndAlign){answer[0],answer[1]};
}
- (MTLSizeAndAlign)heapBufferSizeAndAlignWithLength:(NSUInteger)length options:(MTLResourceOptions)options {
 uint64_t answer[2]={0,0};
 if(!nmb_device_heap_buffer_size_and_align(gDevice,length,(uint64_t)options,answer))
  NBLOG(@"native-metal: heap buffer size refused: %@",lastError());
 return (MTLSizeAndAlign){answer[0],answer[1]};
}
- (id)newResourceGroupFromResources:(const id __unsafe_unretained [])resources count:(NSUInteger)count {
''')

# MPS resource sizing uses the runtime's existing creation layout.
edit('bridge/NativeMetalObjects.h',
     r'''NmbTexture *nmb_texture_new(NmbDevice *, const struct NmbTextureDescriptor *);
''',
     r'''/* Standard MTLDevice heap queries: the runtime's creation layout, not a guessed size.
 * Returns false with nmb_last_error on refusal; output is two writable u64 words. */
bool nmb_device_heap_texture_size_and_align(const NmbDevice *, const struct NmbTextureDescriptor *, uint64_t out[2]);
bool nmb_device_heap_buffer_size_and_align(const NmbDevice *, uint64_t length, uint64_t options, uint64_t out[2]);
NmbTexture *nmb_texture_new(NmbDevice *, const struct NmbTextureDescriptor *);
''')

# MPS returns temporary ranges through the runtime's real aliasing state.
edit('bridge/NativeMetalObjects.rs',
     r'''// ---- textures ----
''',
     r'''/// Resource aliasing delegates to the runtime's heap range ownership, including views.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_make_aliasable(texture: *const NmbTexture) -> bool {
    guard(false, || {
        get(texture)?.0.make_aliasable().map_err(e("make aliasable"))?;
        Ok(true)
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_is_aliasable(texture: *const NmbTexture) -> bool {
    guard(false, || Ok(get(texture)?.0.is_aliasable()))
}

#[unsafe(no_mangle)]
pub extern "C" fn nmb_buffer_make_aliasable(buffer: *const NmbBuffer) -> bool {
    guard(false, || {
        get(buffer)?.0.make_aliasable().map_err(e("make aliasable"))?;
        Ok(true)
    })
}

#[unsafe(no_mangle)]
pub extern "C" fn nmb_buffer_is_aliasable(buffer: *const NmbBuffer) -> bool {
    guard(false, || Ok(get(buffer)?.0.is_aliasable()))
}

// ---- textures ----
''')

# MPS returns temporary ranges through the runtime's real aliasing state.
edit('bridge/NativeMetalObjects.h',
     r'''NmbTexture *nmb_texture_new(NmbDevice *, const struct NmbTextureDescriptor *);
''',
     r'''/* MTLResource lifetime: an automatic heap can reuse the range after makeAliasable.
 * Failures keep the runtime error; isAliasable reports actual state for views too. */
bool nmb_texture_make_aliasable(const NmbTexture *);
bool nmb_texture_is_aliasable(const NmbTexture *);
bool nmb_buffer_make_aliasable(const NmbBuffer *);
bool nmb_buffer_is_aliasable(const NmbBuffer *);
NmbTexture *nmb_texture_new(NmbDevice *, const struct NmbTextureDescriptor *);
''')

# MPS returns temporary ranges through the runtime's real aliasing state.
edit('NativeBridgeMetalFrontend.mm',
     r'''- (BOOL)isAliasable {return NO;} \
''',
     r'''''')

# MPS returns temporary ranges through the runtime's real aliasing state.
edit('NativeBridgeMetalFrontend.mm',
     r'''@implementation NBBuffer
''',
     r'''@implementation NBBuffer
// Releasing an MPS temporary resource returns its automatic-heap range once.
- (BOOL)isAliasable {return nmb_buffer_is_aliasable(_handle);}
- (void)makeAliasable {if(!nmb_buffer_make_aliasable(_handle))NBLOG(@"native-metal: buffer makeAliasable: %@",lastError());}
''')

# MPS returns temporary ranges through the runtime's real aliasing state.
edit('NativeBridgeMetalFrontend.mm',
     r'''@implementation NBTexture
''',
     r'''@implementation NBTexture
// Views share the runtime alias state; a local boolean cannot model the heap lifetime.
- (BOOL)isAliasable {return nmb_texture_is_aliasable(_handle);}
- (void)makeAliasable {if(!nmb_texture_make_aliasable(_handle))NBLOG(@"native-metal: texture makeAliasable: %@",lastError());}
''')

# Keep MTLResource accounting and hazard answers tied to runtime metadata.
edit('bridge/NativeMetalObjects.rs',
     r'''// ---- textures ----
''',
     r'''// ---- textures ----
/// MTLResource queries use recorded backing metadata and resolved hazard tracking,
/// never a descriptor-size estimate or a constant aliasability answer.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_allocated_size(texture: *const NmbTexture) -> u64 {
    guard(0, || get(texture)?.0.backing_allocation_size().ok_or_else(|| "texture backing size is not recorded".to_owned()))
}
#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_hazard_tracking_mode(texture: *const NmbTexture) -> u64 {
    guard(0, || Ok(get(texture)?.0.hazard_tracking_mode().raw()))
}
#[unsafe(no_mangle)]
pub extern "C" fn nmb_buffer_hazard_tracking_mode(buffer: *const NmbBuffer) -> u64 {
    guard(0, || Ok(get(buffer)?.0.hazard_tracking_mode().raw()))
}
''')

# Keep MTLResource accounting and hazard answers tied to runtime metadata.
edit('bridge/NativeMetalObjects.h',
     r'''bool nmb_buffer_is_aliasable(const NmbBuffer *);
''',
     r'''bool nmb_buffer_is_aliasable(const NmbBuffer *);
uint64_t nmb_texture_allocated_size(const NmbTexture *);
uint64_t nmb_texture_hazard_tracking_mode(const NmbTexture *);
uint64_t nmb_buffer_hazard_tracking_mode(const NmbBuffer *);
''')

# Keep MTLResource accounting and hazard answers tied to runtime metadata.
edit('NativeBridgeMetalFrontend.mm',
     r'''@implementation NBBuffer
''',
     r'''@implementation NBBuffer
- (MTLHazardTrackingMode)hazardTrackingMode {return (MTLHazardTrackingMode)nmb_buffer_hazard_tracking_mode(_handle);}
''')

# Keep MTLResource accounting and hazard answers tied to runtime metadata.
edit('NativeBridgeMetalFrontend.mm',
     r'''- (MTLHazardTrackingMode)hazardTrackingMode {return (MTLHazardTrackingMode)((_options&MTLResourceHazardTrackingModeMask)>>MTLResourceHazardTrackingModeShift);}
- (void)didModifyRange:''',
     r'''- (void)didModifyRange:''')

# Keep MTLResource accounting and hazard answers tied to runtime metadata.
edit('NativeBridgeMetalFrontend.mm',
     r'''@implementation NBTexture
''',
     r'''@implementation NBTexture
- (NSUInteger)allocatedSize {return nmb_texture_allocated_size(_handle);}
- (MTLHazardTrackingMode)hazardTrackingMode {return (MTLHazardTrackingMode)nmb_texture_hazard_tracking_mode(_handle);}
''')

# Preserve the Core Image drawable query through the real runtime descriptor.
edit('bridge/NativeMetalObjects.h',
     '    uint64_t type, format, width, height, depth, levels, samples, array_length, usage, options;\n',
     '    uint64_t type, format, width, height, depth, levels, samples, array_length, usage, options;\n    /* Core Image queries this SPI on render targets; preserve it through views. */\n    uint64_t is_drawable;\n')
edit('bridge/NativeMetalObjects.rs',
     '    pub usage: u64,\n    pub options: u64,\n',
     '    pub usage: u64,\n    pub options: u64,\n    pub is_drawable: u64,\n')
edit('bridge/NativeMetalObjects.rs',
     '    out.resource_options = options(d.options)?;\n',
     '    out.resource_options = options(d.options)?;\n    out.is_drawable = d.is_drawable != 0;\n')
edit('bridge/NativeMetalObjects.rs',
     '            options: t.resource_options().raw(),\n',
     '            options: t.resource_options().raw(),\n            is_drawable: u64::from(t.is_drawable()),\n')
edit('NativeBridgeMetalFrontend.mm',
     ' *out={(uint64_t)d.textureType,(uint64_t)d.pixelFormat,d.width,d.height,d.depth,d.mipmapLevelCount,d.sampleCount,d.arrayLength,(uint64_t)d.usage,(uint64_t)d.resourceOptions};\n',
     ' // isDrawable is an iOS SPI, absent from the public descriptor declaration.\n SEL drawable=sel_registerName("isDrawable");\n const BOOL isDrawable=[d respondsToSelector:drawable]?((BOOL(*)(id,SEL))[d methodForSelector:drawable])(d,drawable):NO;\n *out={(uint64_t)d.textureType,(uint64_t)d.pixelFormat,d.width,d.height,d.depth,d.mipmapLevelCount,d.sampleCount,d.arrayLength,(uint64_t)d.usage,(uint64_t)d.resourceOptions,(uint64_t)isDrawable};\n')
edit('NativeBridgeMetalFrontend.mm',
     '- (BOOL)isFramebufferOnly {return NO;}\n',
     '- (BOOL)isFramebufferOnly {return NO;}\n- (BOOL)isDrawable {return _descriptor.is_drawable!=0;}\n')

# Keep the full shadow refusal visible, bounded by the actual diagnostic buffer.
edit('NativeBridgeMetalFrontend.mm',
     '  char warning[200];size_t n=nmb_last_warning(warning,sizeof warning);\n  if(n){row("W %.86s",warning);if(n>86)row("Wb %.86s",warning+86);}\n',
     '  char warning[400];size_t n=nmb_last_warning(warning,sizeof warning);\n  n=MIN(n,sizeof warning-1); // copy_out returns the full required length, possibly truncated.\n  if(n){row("W %.86s",warning);\n   for(size_t at=86;at<n;at+=86)row("Wc %zu %.86s",at,warning+at);}\n')
edit('bridge/NativeMetalObjects.rs',
     '            *last = message.text.chars().take(160).collect();\n',
     "            // The shadow announcements' refusal names run past 160 characters; keep enough\n            // for the status dump's rows to carry the whole reason.\n            *last = message.text.chars().take(384).collect();\n")

# Preserve render-pass resolve destinations, subresources, filters and store options.
edit('bridge/NativeMetalObjects.h',
     '    double clear[4]; /* color, or clear[0] depth / stencil */\n',
     '    double clear[4]; /* color, or clear[0] depth / stencil */\n    /* The resolve destination is an independent resource retained by this pass. */\n    NmbTexture *resolve_texture;\n    uint64_t resolve_level, resolve_slice, resolve_depth_plane, resolve_filter, store_options;\n')
edit('bridge/NativeMetalObjects.rs',
     '    pub clear: [f64; 4],\n',
     '    pub clear: [f64; 4],\n    pub resolve_texture: *mut NmbTexture,\n    pub resolve_level: u64,\n    pub resolve_slice: u64,\n    pub resolve_depth_plane: u64,\n    pub resolve_filter: u64,\n    pub store_options: u64,\n')
edit('bridge/NativeMetalObjects.rs',
     '    SamplerState, ScissorRect, SharedEvent, SharedEventListener, Size, StoreAction, Texture, TextureDescriptor, Viewport, VisibilityResultMode,',
     '    SamplerState, ScissorRect, SharedEvent, SharedEventListener, Size, StoreAction, StoreActionOptions, MultisampleDepthResolveFilter, MultisampleStencilResolveFilter, Texture, TextureDescriptor, Viewport, VisibilityResultMode,')
edit('bridge/NativeMetalObjects.rs',
     'fn texture_of(a: &NmbPassAttachment) -> Result<Option<Texture>, String> {\n    if a.texture.is_null() { Ok(None) } else { Ok(Some(get(a.texture)?.0.clone())) }\n}',
     'fn texture_from_handle(handle: *mut NmbTexture) -> Result<Option<Texture>, String> {\n    if handle.is_null() { Ok(None) } else { Ok(Some(get(handle)?.0.clone())) }\n}\n\nfn texture_of(a: &NmbPassAttachment) -> Result<Option<Texture>, String> {\n    texture_from_handle(a.texture)\n}')
edit('bridge/NativeMetalObjects.rs',
     '            slot.texture = texture_of(a)?;\n',
     '            slot.texture = texture_of(a)?;\n            slot.resolve_texture = texture_from_handle(a.resolve_texture)?;\n            slot.resolve_level = a.resolve_level;\n            slot.resolve_slice = a.resolve_slice;\n            slot.resolve_depth_plane = a.resolve_depth_plane;\n            slot.store_action_options = StoreActionOptions::from_raw(a.store_options).map_err(e("color store options"))?;\n')
edit('bridge/NativeMetalObjects.rs',
     '        depth.texture = texture_of(&d.depth)?;\n',
     '        depth.texture = texture_of(&d.depth)?;\n        depth.resolve_texture = texture_from_handle(d.depth.resolve_texture)?;\n        depth.resolve_level = d.depth.resolve_level;\n        depth.resolve_slice = d.depth.resolve_slice;\n        depth.resolve_depth_plane = d.depth.resolve_depth_plane;\n        depth.store_action_options = StoreActionOptions::from_raw(d.depth.store_options).map_err(e("depth store options"))?;\n        depth.depth_resolve_filter = MultisampleDepthResolveFilter::from_raw(d.depth.resolve_filter).map_err(e("depth resolve filter"))?;\n')
edit('bridge/NativeMetalObjects.rs',
     '        stencil.texture = texture_of(&d.stencil)?;\n',
     '        stencil.texture = texture_of(&d.stencil)?;\n        stencil.resolve_texture = texture_from_handle(d.stencil.resolve_texture)?;\n        stencil.resolve_level = d.stencil.resolve_level;\n        stencil.resolve_slice = d.stencil.resolve_slice;\n        stencil.resolve_depth_plane = d.stencil.resolve_depth_plane;\n        stencil.store_action_options = StoreActionOptions::from_raw(d.stencil.store_options).map_err(e("stencil store options"))?;\n        stencil.stencil_resolve_filter = MultisampleStencilResolveFilter::from_raw(d.stencil.resolve_filter).map_err(e("stencil resolve filter"))?;\n')
edit('NativeBridgeMetalFrontend.mm',
     '  out.texture=texture.handle;out.level=a.level;out.slice=a.slice;out.depth_plane=a.depthPlane;out.load=a.loadAction;out.store=a.storeAction;\n',
     '  out.texture=texture.handle;out.level=a.level;out.slice=a.slice;out.depth_plane=a.depthPlane;out.load=a.loadAction;out.store=a.storeAction;\n  out.store_options=a.storeActionOptions;\n  if(a.resolveTexture) {\n   if(![(id)a.resolveTexture isKindOfClass:[NBTexture class]])return false;\n   NBTexture *resolve=(NBTexture *)a.resolveTexture;\n   [_resourceOwners addObject:resolve];\n   if(resolve.surface)[_surfaces addObject:resolve];\n   out.resolve_texture=resolve.handle;out.resolve_level=a.resolveLevel;\n   out.resolve_slice=a.resolveSlice;out.resolve_depth_plane=a.resolveDepthPlane;\n   // Bounded compatibility evidence identifies actual callers of this previously lost output.\n   static std::atomic<unsigned> resolved{0};\n   if(resolved.fetch_add(1,std::memory_order_relaxed)<4)\n    NSLog(@"NEZHA Metal: resolve samples=%lu format=%lu level=%lu slice=%lu store=%lu",(unsigned long)a.texture.sampleCount,\n          (unsigned long)a.texture.pixelFormat,(unsigned long)a.resolveLevel,(unsigned long)a.resolveSlice,(unsigned long)a.storeAction);\n  }\n')
edit('NativeBridgeMetalFrontend.mm',
     ' pass.depth.clear[0]=d.depthAttachment.clearDepth;pass.stencil.clear[0]=d.stencilAttachment.clearStencil;\n',
     ' pass.depth.clear[0]=d.depthAttachment.clearDepth;pass.stencil.clear[0]=d.stencilAttachment.clearStencil;\n pass.depth.resolve_filter=d.depthAttachment.depthResolveFilter;\n pass.stencil.resolve_filter=d.stencilAttachment.stencilResolveFilter;\n')

# Preserve asynchronous failures even when a framework omits reading commandBuffer.error.
edit('bridge/NativeMetalObjects.rs',
     '    let Some(report) = done.execution_report() else { return };\n',
     '    // Frameworks do not always inspect MTLCommandBuffer.error after completion.\n    // Surface genuine asynchronous failures even when no execution report exists.\n    if let Some(error) = done.error_description() {\n        static ERRORS: std::sync::Mutex<Vec<String>> = std::sync::Mutex::new(Vec::new());\n        let report = if let Ok(mut errors) = ERRORS.lock() {\n            if errors.len() >= 16 || errors.contains(&error) { false }\n            else { errors.push(error.clone()); true }\n        } else { true };\n        if report {\n            let text = format!("NEZHA Metal: asynchronous command failure: {error}\\0");\n            // SAFETY: the format and message are NUL-terminated for this call.\n            unsafe { syslog(3, c"%s".as_ptr(), text.as_ptr().cast::<c_char>()) };\n        }\n    }\n    let Some(report) = done.execution_report() else { return };\n')
edit('bridge/NativeMetalObjects.rs',
     '        let _ = command_buffer.add_completed_handler(Box::new(nmb_report_execution));\n',
     '        command_buffer.add_completed_handler(Box::new(nmb_report_execution)).map_err(e("execution report handler"))?;\n')

# Opt-in bounded completed IOSurface samples; no application pixels are changed.
edit('NativeBridgeMetalFrontend.mm',
     'static void flushWrapped(NSSet *owners) {',
     '// Default-off, bounded output observation for wallpaper bring-up. Samples only\n// shared IOSurface bytes after GPU completion and the normal cache maintenance.\nstatic char gObservedOutputsKey;\nstatic bool observeSurfaceOutputs() {\n static const bool enabled=[] {\n  char args[2048]={};size_t length=sizeof args-1;\n  if(sysctlbyname("kern.bootargs",args,&length,nullptr,0))return false;\n  static const char token[]="nezha_surface_observe=1";\n  const char *at=strstr(args,token);\n  return at && (at==args || at[-1]==\' \') && (at[sizeof(token)-1]==0 || at[sizeof(token)-1]==\' \');\n }();\n return enabled;\n}\nstatic void observeSurfaceTarget(id command,NBTexture *texture) {\n if(!observeSurfaceOutputs() || !texture.surface)return;\n NSMutableSet *targets=objc_getAssociatedObject(command,&gObservedOutputsKey);\n if(!targets){targets=[NSMutableSet set];objc_setAssociatedObject(command,&gObservedOutputsKey,targets,OBJC_ASSOCIATION_RETAIN_NONATOMIC);}\n [targets addObject:texture];\n}\nstatic void reportSurfaceOutputs(id command,MTLCommandBufferStatus status) {\n if(!observeSurfaceOutputs())return;\n NSSet *targets=objc_getAssociatedObject(command,&gObservedOutputsKey);\n static std::mutex mutex;\n static std::unordered_map<uint64_t,unsigned> counts;\n for(NBTexture *texture in targets) {\n  IOSurfaceRef surface=texture.surface;\n  const size_t width=IOSurfaceGetWidth(surface),height=IOSurfaceGetHeight(surface),row=IOSurfaceGetBytesPerRow(surface),size=IOSurfaceGetAllocSize(surface);\n  const uint64_t key=(uint64_t)texture.pixelFormat<<32 | (uint64_t)width<<16 | height;\n  unsigned sample=0;\n  {std::lock_guard<std::mutex> lock(mutex);\n   auto found=counts.find(key);\n   if(found==counts.end()){if(counts.size()>=12)continue;found=counts.emplace(key,0).first;}\n   if(found->second>=3)continue;sample=++found->second;\n  }\n  if(!width || !height || width>16384 || height>16384 || row<width*4 || height>size/row || !IOSurfaceGetBaseAddress(surface))continue;\n  uint32_t words[16]={};const uint8_t *base=(const uint8_t *)IOSurfaceGetBaseAddress(surface);\n  for(unsigned y=0;y<4;y++)for(unsigned x=0;x<4;x++)\n   memcpy(&words[y*4+x],base+((2*y+1)*height/8)*row+((2*x+1)*width/8)*4,4);\n  NSLog(@"NEZHA Surface: output format=%lu %zux%zu row=%zu planes=%zu sample=%u status=%lu words=%08x,%08x,%08x,%08x/%08x,%08x,%08x,%08x/%08x,%08x,%08x,%08x/%08x,%08x,%08x,%08x",\n   (unsigned long)texture.pixelFormat,width,height,row,IOSurfaceGetPlaneCount(surface),sample,(unsigned long)status,\n   words[0],words[1],words[2],words[3],words[4],words[5],words[6],words[7],words[8],words[9],words[10],words[11],words[12],words[13],words[14],words[15]);\n }\n objc_setAssociatedObject(command,&gObservedOutputsKey,nil,OBJC_ASSOCIATION_RETAIN_NONATOMIC);\n}\n\nstatic void flushWrapped(NSSet *owners) {')
edit('NativeBridgeMetalFrontend.mm',
     '  [_resourceOwners addObject:texture];\n  out.texture=texture.handle;',
     '  [_resourceOwners addObject:texture];\n  observeSurfaceTarget(self,texture);\n  out.texture=texture.handle;')
edit('NativeBridgeMetalFrontend.mm',
     '   [_resourceOwners addObject:resolve];\n   if(resolve.surface)',
     '   [_resourceOwners addObject:resolve];\n   observeSurfaceTarget(self,resolve);\n   if(resolve.surface)')
edit('NativeBridgeMetalFrontend.mm',
     '- (void)finish {\n flushSurfaces(_surfaces);\n',
     '- (void)finish {\n flushSurfaces(_surfaces);\n reportSurfaceOutputs(self,[(id<MTLCommandBuffer>)self status]);\n')

# Observe blit destinations and writable compute surfaces as well as render targets.
edit('NativeBridgeMetalFrontend.mm',
     ' if(texture)[_commandBuffer.resourceOwners addObject:texture];\n',
     ' if(texture)[_commandBuffer.resourceOwners addObject:texture];\n if(texture && stage==0 && (texture.usage&MTLTextureUsageShaderWrite))observeSurfaceTarget(_commandBuffer,(NBTexture *)texture);\n')
edit('NativeBridgeMetalFrontend.mm',
     ' if(![self textures:NB_OBJECTS(s,d) count:2])return;\n const uint64_t a[3]={so.x,so.y,so.z}',
     ' if(![self textures:NB_OBJECTS(s,d) count:2])return;\n observeSurfaceTarget(self.commandBuffer,(NBTexture *)d);\n const uint64_t a[3]={so.x,so.y,so.z}')
edit('NativeBridgeMetalFrontend.mm',
     ' if(![self buffers:NB_OBJECTS(s) count:1] || ![self textures:NB_OBJECTS(d) count:1])return;\n',
     ' if(![self buffers:NB_OBJECTS(s) count:1] || ![self textures:NB_OBJECTS(d) count:1])return;\n observeSurfaceTarget(self.commandBuffer,(NBTexture *)d);\n')

# Weather uses the same audited bounded CoreLocation authorization query as Aegir.
edit('NativeAdrenoMetalPlugin.mm',
     '    if (!strcmp(getprogname(), "AegirPoster")) {\n        gClientPort = port;\n',
     '    if (!strcmp(getprogname(), "AegirPoster") || !strcmp(getprogname(), "WeatherPoster")) {\n        gClientPort = port;\n')

# Swizzle bridge (data-a-live, 2026-11-04). metal2vulkan already implements
# TextureSwizzleChannels, TextureDescriptor.swizzle, Texture::swizzle() and
# Texture::new_texture_view_with_swizzle; the iOS 16 frontend used to drop
# MTLTextureDescriptor.swizzle, had no MTLTexture.swizzle getter and no
# ...:swizzle: view selector, so a non-identity swizzle silently read as identity.
# Carry Metal's four raw channel values (0..5) across the C/Rust ABI. A zeroed
# word is MTLTextureSwizzleZero, not the identity swizzle, so every creator fills
# them from the descriptor. This reuses an existing runtime capability; it does
# not change the runtime, Turnip, or any unrelated driver.
edit('bridge/NativeMetalObjects.h',
     r'''struct NmbTextureDescriptor {
    uint64_t type, format, width, height, depth, levels, samples, array_length, usage, options;
    /* Core Image queries this SPI on render targets; preserve it through views. */
    uint64_t is_drawable;
};''',
     r'''struct NmbTextureDescriptor {
    uint64_t type, format, width, height, depth, levels, samples, array_length, usage, options;
    /* Core Image queries this SPI on render targets; preserve it through views. */
    uint64_t is_drawable;
    /* MTLTextureSwizzle raw values (0..5) in red/green/blue/alpha order. */
    uint64_t swizzle[4];
};''')
edit('bridge/NativeMetalObjects.h',
     r'''/* whole: a view of every level and slice as format; else levels and slices are {start, count} and kind the view's texture type. */
NmbTexture *nmb_texture_new_view(NmbTexture *, uint64_t format, uint64_t kind, const uint64_t levels[2], const uint64_t slices[2], bool whole);''',
     r'''/* whole: a view of every level and slice as format; else levels and slices are {start, count} and kind the view's texture type. */
NmbTexture *nmb_texture_new_view(NmbTexture *, uint64_t format, uint64_t kind, const uint64_t levels[2], const uint64_t slices[2], bool whole);
/* A view whose own swizzle replaces the texture's; swizzle holds four MTLTextureSwizzle raw values. */
NmbTexture *nmb_texture_new_view_with_swizzle(NmbTexture *, uint64_t format, uint64_t kind,
                                              const uint64_t levels[2], const uint64_t slices[2],
                                              const uint64_t swizzle[4]);''')

edit('bridge/NativeMetalObjects.rs',
     r'''    PrimitiveType, RenderStages, SamplerAddressMode, SamplerBorderColor, SamplerMinMagFilter, SamplerMipFilter, StencilOperation, TextureType,
    TextureUsage, TriangleFillMode, VertexFormat, VertexStepFunction, Winding,''',
     r'''    PrimitiveType, RenderStages, SamplerAddressMode, SamplerBorderColor, SamplerMinMagFilter, SamplerMipFilter, StencilOperation,
    TextureSwizzle, TextureSwizzleChannels, TextureType, TextureUsage, TriangleFillMode, VertexFormat, VertexStepFunction, Winding,''')
edit('bridge/NativeMetalObjects.rs',
     r'''#[repr(C)]
pub struct NmbTextureDescriptor {
    pub kind: u64,
    pub format: u64,
    pub width: u64,
    pub height: u64,
    pub depth: u64,
    pub levels: u64,
    pub samples: u64,
    pub array_length: u64,
    pub usage: u64,
    pub options: u64,
    pub is_drawable: u64,
}''',
     r'''#[repr(C)]
pub struct NmbTextureDescriptor {
    pub kind: u64,
    pub format: u64,
    pub width: u64,
    pub height: u64,
    pub depth: u64,
    pub levels: u64,
    pub samples: u64,
    pub array_length: u64,
    pub usage: u64,
    pub options: u64,
    pub is_drawable: u64,
    pub swizzle: [u64; 4],
}''')
edit('bridge/NativeMetalObjects.rs',
     r'''fn texture_descriptor(d: *const NmbTextureDescriptor) -> Result<TextureDescriptor, String> {
    let d = get(d)?;
    let mut out = TextureDescriptor::default();
    out.texture_type = raw::<TextureType>(d.kind, "texture type")?;
    out.pixel_format = raw::<PixelFormat>(d.format, "pixel format")?;
    out.width = d.width;
    out.height = d.height;
    out.depth = d.depth;
    out.mipmap_level_count = d.levels;
    out.sample_count = d.samples;
    out.array_length = d.array_length;
    out.usage = raw::<TextureUsage>(d.usage, "texture usage")?;
    out.resource_options = options(d.options)?;
    out.is_drawable = d.is_drawable != 0;
    Ok(out)
}''',
     r'''/// Metal's four `MTLTextureSwizzle` raw values (0..5) as the runtime's checked channels.
/// An unknown value fails the call; the runtime's own validation decides what usage a
/// swizzled texture may have (it refuses ShaderWrite/RenderTarget without writeSwizzleEnabled).
fn texture_swizzle_channels(values: [u64; 4]) -> Result<TextureSwizzleChannels, String> {
    Ok(TextureSwizzleChannels::new(
        raw::<TextureSwizzle>(values[0], "texture swizzle red")?,
        raw::<TextureSwizzle>(values[1], "texture swizzle green")?,
        raw::<TextureSwizzle>(values[2], "texture swizzle blue")?,
        raw::<TextureSwizzle>(values[3], "texture swizzle alpha")?,
    ))
}

fn texture_descriptor(d: *const NmbTextureDescriptor) -> Result<TextureDescriptor, String> {
    let d = get(d)?;
    let mut out = TextureDescriptor::default();
    out.texture_type = raw::<TextureType>(d.kind, "texture type")?;
    out.pixel_format = raw::<PixelFormat>(d.format, "pixel format")?;
    out.width = d.width;
    out.height = d.height;
    out.depth = d.depth;
    out.mipmap_level_count = d.levels;
    out.sample_count = d.samples;
    out.array_length = d.array_length;
    out.usage = raw::<TextureUsage>(d.usage, "texture usage")?;
    out.resource_options = options(d.options)?;
    out.is_drawable = d.is_drawable != 0;
    out.swizzle = texture_swizzle_channels(d.swizzle)?;
    Ok(out)
}''')
edit('bridge/NativeMetalObjects.rs',
     r'''/// A view of `texture`: of the whole texture as `format` where `whole`, else of `levels` and `slices` (start, count) as `format` and `kind`.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_new_view(texture: *mut NmbTexture, format: u64, kind: u64, levels: *const u64, slices: *const u64, whole: bool)
                                       -> *mut NmbTexture {
    guard(std::ptr::null_mut(), || {
        let t = &get(texture)?.0;
        let format = raw::<PixelFormat>(format, "pixel format")?;
        let view = if whole {
            t.new_texture_view(format)
        } else {
            if levels.is_null() || slices.is_null() {
                return Err("null levels or slices".into());
            }
            // SAFETY: the header's two words each.
            let (l, s) = unsafe { (std::slice::from_raw_parts(levels, 2), std::slice::from_raw_parts(slices, 2)) };
            let ranges = (l[0]..l[0].checked_add(l[1]).ok_or("levels overflow")?, s[0]..s[0].checked_add(s[1]).ok_or("slices overflow")?);
            t.new_texture_view_with_levels_slices(format, raw::<TextureType>(kind, "texture type")?, ranges.0, ranges.1)
        };
        Ok(boxed(NmbTexture(view.map_err(e("texture view"))?)))
    })
}''',
     r'''/// A view of `texture`: of the whole texture as `format` where `whole`, else of `levels` and `slices` (start, count) as `format` and `kind`.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_new_view(texture: *mut NmbTexture, format: u64, kind: u64, levels: *const u64, slices: *const u64, whole: bool)
                                       -> *mut NmbTexture {
    guard(std::ptr::null_mut(), || {
        let t = &get(texture)?.0;
        let format = raw::<PixelFormat>(format, "pixel format")?;
        let view = if whole {
            t.new_texture_view(format)
        } else {
            let (l, s) = view_ranges(levels, slices)?;
            t.new_texture_view_with_levels_slices(format, raw::<TextureType>(kind, "texture type")?, l, s)
        };
        Ok(boxed(NmbTexture(view.map_err(e("texture view"))?)))
    })
}

/// The header's two words each, checked for overflow before the runtime sees a range.
fn view_ranges(levels: *const u64, slices: *const u64) -> Result<(std::ops::Range<u64>, std::ops::Range<u64>), String> {
    if levels.is_null() || slices.is_null() {
        return Err("null levels or slices".into());
    }
    // SAFETY: the header's two words each.
    let (l, s) = unsafe { (std::slice::from_raw_parts(levels, 2), std::slice::from_raw_parts(slices, 2)) };
    Ok((l[0]..l[0].checked_add(l[1]).ok_or("levels overflow")?,
        s[0]..s[0].checked_add(s[1]).ok_or("slices overflow")?))
}

/// `newTextureViewWithPixelFormat:textureType:levels:slices:swizzle:` — the runtime's own
/// swizzled view. The runtime checks the swizzle and the resulting usage itself.
#[unsafe(no_mangle)]
pub extern "C" fn nmb_texture_new_view_with_swizzle(texture: *mut NmbTexture, format: u64, kind: u64,
                                                    levels: *const u64, slices: *const u64, swizzle: *const u64)
                                                    -> *mut NmbTexture {
    guard(std::ptr::null_mut(), || {
        let t = &get(texture)?.0;
        let format = raw::<PixelFormat>(format, "pixel format")?;
        let kind = raw::<TextureType>(kind, "texture type")?;
        let (l, s) = view_ranges(levels, slices)?;
        if swizzle.is_null() {
            return Err("null swizzle".into());
        }
        // SAFETY: the header's four words.
        let w = unsafe { std::slice::from_raw_parts(swizzle, 4) };
        let channels = texture_swizzle_channels([w[0], w[1], w[2], w[3]])?;
        Ok(boxed(NmbTexture(t.new_texture_view_with_swizzle(format, kind, l, s, channels)
            .map_err(e("swizzled texture view"))?)))
    })
}''')
edit('bridge/NativeMetalObjects.rs',
     r'''            options: t.resource_options().raw(),
            is_drawable: u64::from(t.is_drawable()),
        };''',
     r'''            options: t.resource_options().raw(),
            is_drawable: u64::from(t.is_drawable()),
            swizzle: {
                let s = t.swizzle();
                [s.red.as_raw() as u64, s.green.as_raw() as u64, s.blue.as_raw() as u64, s.alpha.as_raw() as u64]
            },
        };''')

edit('NativeBridgeMetalFrontend.mm',
     r'''static bool textureDescriptor(MTLTextureDescriptor *d,NmbTextureDescriptor *out) {
 if(!d)return false;
 // isDrawable is an iOS SPI, absent from the public descriptor declaration.
 SEL drawable=sel_registerName("isDrawable");
 const BOOL isDrawable=[d respondsToSelector:drawable]?((BOOL(*)(id,SEL))[d methodForSelector:drawable])(d,drawable):NO;
 *out={(uint64_t)d.textureType,(uint64_t)d.pixelFormat,d.width,d.height,d.depth,d.mipmapLevelCount,d.sampleCount,d.arrayLength,(uint64_t)d.usage,(uint64_t)d.resourceOptions,(uint64_t)isDrawable};
 return true;
}''',
     r'''static bool textureDescriptor(MTLTextureDescriptor *d,NmbTextureDescriptor *out) {
 if(!d)return false;
 // isDrawable is an iOS SPI, absent from the public descriptor declaration.
 SEL drawable=sel_registerName("isDrawable");
 const BOOL isDrawable=[d respondsToSelector:drawable]?((BOOL(*)(id,SEL))[d methodForSelector:drawable])(d,drawable):NO;
 *out={(uint64_t)d.textureType,(uint64_t)d.pixelFormat,d.width,d.height,d.depth,d.mipmapLevelCount,d.sampleCount,d.arrayLength,(uint64_t)d.usage,(uint64_t)d.resourceOptions,(uint64_t)isDrawable};
 // Metal's descriptor defaults to identity (2/3/4/5). A zero-filled ABI word means
 // MTLTextureSwizzleZero, so always copy the real channels: an aggregate initializer's
 // tail would silently turn every sampled channel black.
 const MTLTextureSwizzleChannels s=d.swizzle;
 out->swizzle[0]=(uint64_t)s.red;out->swizzle[1]=(uint64_t)s.green;
 out->swizzle[2]=(uint64_t)s.blue;out->swizzle[3]=(uint64_t)s.alpha;
 return true;
}''')
edit('NativeBridgeMetalFrontend.mm',
     r'''- (BOOL)isDrawable {return _descriptor.is_drawable!=0;}''',
     r'''- (BOOL)isDrawable {return _descriptor.is_drawable!=0;}
// nmb_texture_describe reports the runtime's own swizzle (DEFAULT for a plain texture).
- (MTLTextureSwizzleChannels)swizzle {
 const MTLTextureSwizzleChannels s={
  (MTLTextureSwizzle)_descriptor.swizzle[0],(MTLTextureSwizzle)_descriptor.swizzle[1],
  (MTLTextureSwizzle)_descriptor.swizzle[2],(MTLTextureSwizzle)_descriptor.swizzle[3]};
 return s;
}''')
edit('NativeBridgeMetalFrontend.mm',
     r'''- (id<MTLTexture>)newTextureViewWithPixelFormat:(MTLPixelFormat)format textureType:(MTLTextureType)type levels:(NSRange)levels slices:(NSRange)slices {
 const uint64_t l[2]={levels.location,levels.length},s[2]={slices.location,slices.length};
 return [self view:nmb_texture_new_view(_handle,format,type,l,s,false)];
}''',
     r'''- (id<MTLTexture>)newTextureViewWithPixelFormat:(MTLPixelFormat)format textureType:(MTLTextureType)type levels:(NSRange)levels slices:(NSRange)slices {
 const uint64_t l[2]={levels.location,levels.length},s[2]={slices.location,slices.length};
 return [self view:nmb_texture_new_view(_handle,format,type,l,s,false)];
}
- (id<MTLTexture>)newTextureViewWithPixelFormat:(MTLPixelFormat)format textureType:(MTLTextureType)type levels:(NSRange)levels slices:(NSRange)slices swizzle:(MTLTextureSwizzleChannels)swizzle {
 const uint64_t l[2]={levels.location,levels.length},s[2]={slices.location,slices.length};
 const uint64_t w[4]={(uint64_t)swizzle.red,(uint64_t)swizzle.green,(uint64_t)swizzle.blue,(uint64_t)swizzle.alpha};
 return [self view:nmb_texture_new_view_with_swizzle(_handle,format,type,l,s,w)];
}''')
# A view's descriptor is the only source of MTLTexture.swizzle. Zero it first so a
# refused describe leaves deterministic fields instead of stack garbage in the getter.
edit('NativeBridgeMetalFrontend.mm',
     r''' NBTexture *view=[NBTexture new];view.handle=handle;NmbTextureDescriptor descriptor;nmb_texture_describe(handle,&descriptor);view.descriptor=descriptor;''',
     r''' NBTexture *view=[NBTexture new];view.handle=handle;
 // Zero first: describe can refuse, and the swizzle getter must not read stack garbage.
 NmbTextureDescriptor descriptor={};nmb_texture_describe(handle,&descriptor);view.descriptor=descriptor;''')

# Surface output observation (data-a-live, 2026-11-04). The astronomy gallery
# thumbnail is a 302x656 ASTC snapshot. The original observer only accepted
# IOSurface-backed render targets, so it never saw the snapshotter's own target:
# all 42 recorded samples came from backboardd's full-screen surfaces. Accept
# small ordinary textures too, and read a real 4x4 tile through the runtime's
# checked get_bytes (reporting a refusal, never substituting zeros for pixels).
# A dedicated diagnostic component enables this at compile time because the full
# Wi-Fi/FairPlay baseline is 586 bytes and nezha_surface_observe=1 would exceed
# the kernel's 608-byte boot-args field; normal components keep the boot-arg gate.
edit('NativeBridgeMetalFrontend.mm',
     r'''static bool observeSurfaceOutputs() {
 static const bool enabled=[] {
  char args[2048]={};size_t length=sizeof args-1;
  if(sysctlbyname("kern.bootargs",args,&length,nullptr,0))return false;
  static const char token[]="nezha_surface_observe=1";
  const char *at=strstr(args,token);
  return at && (at==args || at[-1]==' ') && (at[sizeof(token)-1]==0 || at[sizeof(token)-1]==' ');
 }();
 return enabled;
}
static void observeSurfaceTarget(id command,NBTexture *texture) {
 if(!observeSurfaceOutputs() || !texture.surface)return;''',
     r'''static bool observeSurfaceOutputs() {
#if defined(NEZHA_SURFACE_OBSERVE_BUILTIN)
 return true;
#else
 static const bool enabled=[] {
  char args[2048]={};size_t length=sizeof args-1;
  if(sysctlbyname("kern.bootargs",args,&length,nullptr,0))return false;
  static const char token[]="nezha_surface_observe=1";
  const char *at=strstr(args,token);
  return at && (at==args || at[-1]==' ') && (at[sizeof(token)-1]==0 || at[sizeof(token)-1]==' ');
 }();
 return enabled;
#endif
}
static void observeSurfaceTarget(id command,NBTexture *texture) {
 if(!observeSurfaceOutputs())return;
 // Thumbnail render targets are often ordinary textures. Keep the extra ones
 // small; the report is bounded by format/size and records refused readbacks.
 if(!texture.surface && (texture.width>1024 || texture.height>2048))return;''')
edit('NativeBridgeMetalFrontend.mm',
     r'''static void reportSurfaceOutputs(id command,MTLCommandBufferStatus status) {
 if(!observeSurfaceOutputs())return;
 NSSet *targets=objc_getAssociatedObject(command,&gObservedOutputsKey);
 static std::mutex mutex;
 static std::unordered_map<uint64_t,unsigned> counts;
 for(NBTexture *texture in targets) {
  IOSurfaceRef surface=texture.surface;
  const size_t width=IOSurfaceGetWidth(surface),height=IOSurfaceGetHeight(surface),row=IOSurfaceGetBytesPerRow(surface),size=IOSurfaceGetAllocSize(surface);''',
     r'''static void reportSurfaceOutputs(id command,MTLCommandBufferStatus status) {
 if(!observeSurfaceOutputs())return;
 NSSet *targets=objc_getAssociatedObject(command,&gObservedOutputsKey);
 static std::mutex mutex;
 static std::unordered_map<uint64_t,unsigned> counts,plainCounts;
 for(NBTexture *texture in targets) {
  IOSurfaceRef surface=texture.surface;
  if(!surface) {
   const NSUInteger width=texture.width,height=texture.height;
   const uint64_t key=(uint64_t)texture.pixelFormat<<32 | (uint64_t)width<<16 | height;
   unsigned sample=0;
   {std::lock_guard<std::mutex> lock(mutex);
    auto found=plainCounts.find(key);
    if(found==plainCounts.end()){if(plainCounts.size()>=12)continue;found=plainCounts.emplace(key,0).first;}
    if(found->second>=3)continue;sample=++found->second;
   }
   uint32_t words[16]={};uint8_t tile[4*4*4]={};
   const uint64_t region[6]={width/2,height/2,0,4,4,1};
   const bool read=nmb_texture_get_bytes(texture.handle,tile,16,64,64,region,0,0);
   if(read)memcpy(words,tile,sizeof words);
   NSLog(@"NEZHA Surface: plain target format=%lu %lux%lu usage=%lu sample=%u status=%lu read=%d words=%08x,%08x,%08x,%08x/%08x,%08x,%08x,%08x/%08x,%08x,%08x,%08x/%08x,%08x,%08x,%08x",
    (unsigned long)texture.pixelFormat,(unsigned long)width,(unsigned long)height,(unsigned long)texture.usage,
    sample,(unsigned long)status,(int)read,
    words[0],words[1],words[2],words[3],words[4],words[5],words[6],words[7],
    words[8],words[9],words[10],words[11],words[12],words[13],words[14],words[15]);
   continue;
  }
  const size_t width=IOSurfaceGetWidth(surface),height=IOSurfaceGetHeight(surface),row=IOSurfaceGetBytesPerRow(surface),size=IOSurfaceGetAllocSize(surface);''')


# data-a-live (2026-11-08, P1-C): the frame trace never reached the disk.  Its writer opened one path for
# every injected process (eleven of them) and truncated it at each start, and the directory it names does
# not exist, so open() failed silently.  Create Caches/com.apple.backboardd/m2v/<process> and let each
# process write its own frames.trace and cpu.profile there; the 72-byte record format does not change.
edit('NativeBridgeMetalFrontend.mm',
     '#include <sys/mman.h>',
     '#include <sys/mman.h>\n#include <sys/stat.h>')

edit('NativeBridgeMetalFrontend.mm',
     """  int fd=-1;
  static const char *const paths[]={"/private/var/mobile/Library/Caches/com.apple.backboardd/m2v/frames.trace","/private/var/root/Library/Caches/com.apple.backboardd/m2v/frames.trace"};
  uint32_t written=0,sampled=0;int profileFd=-1;
  static const char *const profilePaths[]={"/private/var/mobile/Library/Caches/com.apple.backboardd/m2v/cpu.profile","/private/var/root/Library/Caches/com.apple.backboardd/m2v/cpu.profile"};""",
     """  int fd=-1;
  char tracePath[256]={},profilePath[256]={};
  {
   const char *proc=getprogname();if(!proc||!*proc)proc="unknown";
   for(const char *base:{"/private/var/mobile/Library/Caches","/private/var/root/Library/Caches"}) {
    char dir[224];
    snprintf(dir,sizeof dir,"%s/com.apple.backboardd",base);mkdir(dir,0777);
    snprintf(dir,sizeof dir,"%s/com.apple.backboardd/m2v",base);mkdir(dir,0777);
    snprintf(dir,sizeof dir,"%s/com.apple.backboardd/m2v/%s",base,proc);mkdir(dir,0777);
    snprintf(tracePath,sizeof tracePath,"%s/frames.trace",dir);
    snprintf(profilePath,sizeof profilePath,"%s/cpu.profile",dir);
    if(!access(dir,W_OK))break;
    tracePath[0]=profilePath[0]=0;
   }
  }
  const char *const profilePaths[2]={profilePath,""};
  uint32_t written=0,sampled=0;int profileFd=-1;""")

edit('NativeBridgeMetalFrontend.mm',
     '   if(fd<0)for(const char *path:paths){fd=open(path,O_WRONLY|O_CREAT|O_TRUNC,0644);if(fd>=0)break;}',
     '   if(fd<0 && tracePath[0])fd=open(tracePath,O_WRONLY|O_CREAT|O_TRUNC,0644);')
