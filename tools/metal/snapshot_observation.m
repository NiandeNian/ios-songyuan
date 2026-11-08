/* Read-only observation of the wallpaper gallery snapshot pipeline.
 *
 * Why this exists: astronomy gallery thumbnails (.atx) contain valid ASTC
 * constant-black blocks with only the ClockKit text layer (rows 18-40), while
 * the large astronomy preview renders correctly. Two earlier on-device
 * experiments ruled out the NativeAdrenoMetal render pass as the producer of
 * those thumbnails. The remaining hypothesis is that ClockKit's Metal resource
 * manager cannot supply a real atlas in the offscreen snapshot context and
 * falls back to a null atlas / nil texture, leaving just the CPU-drawn text.
 *
 * This instrument never changes an argument, return value, asset, descriptor
 * or wallpaper configuration. It is linked only into an explicit opt-in
 * diagnostic shim (--snapshot-observation); an ordinary NezhaStatusBar build
 * does not contain this translation unit at all.
 *
 * Readback is a file receipt, not NSLog: on this port PosterBoard's NSLog does
 * not reach the pinned kernel log (the earlier astronomy repair shim showed
 * the same behaviour and also used receipts). Every process that loads the
 * shim writes its own receipt, so the injection scope is observable as well.
 * Receipts live in /private/var/mobile/Library/Logs/NezhaSnapshotObservation/
 * <boot-uuid>/ and are read back offline through the pinned HFS reader.
 */
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dispatch/dispatch.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#include <syslog.h>
#include <unistd.h>

/* Bounded counters: every hook only observes, never alters control flow. */
static atomic_uint callsSharedDevice, callsSharedQueue, callsNilTexture, callsNullAtlas;
static atomic_uint callsSnapshots, callsSnapshotImages, callsDidTake, callsFailed;
static atomic_uint nilsSharedDevice, nilsSharedQueue;
static atomic_int installed;

static NSLock *observationLock;
static NSMutableArray *observationEvents;
static NSString *receiptPath;
static NSString *observerProcess;
static NSString *bootIdentifier;

static void recordEvent(NSString *line)
{
    [observationLock lock];
    if (observationEvents.count < 80) [observationEvents addObject:line];
    [observationLock unlock];
    syslog(5, "%s", line.UTF8String);
}

#define SNAPLOG(fmt, ...) recordEvent([NSString stringWithFormat:@"NEZHA Snapshot: " fmt, ##__VA_ARGS__])

static const char *classNameOf(id object)
{
    if (!object) return "nil";
    Class cls = object_getClass(object);
    return cls ? class_getName(cls) : "nil-class";
}

static void writeReceipt(void)
{
    if (!receiptPath) return;
    [observationLock lock];
    NSArray *events = [observationEvents copy];
    [observationLock unlock];
    NSDictionary *receipt = @{
        @"boot": bootIdentifier ?: @"unknown",
        @"process": observerProcess ?: @"unknown",
        @"pid": @(getpid()),
        @"events": events ?: @[],
        @"counters": @{
            @"sharedDevice": @(atomic_load(&callsSharedDevice)),
            @"sharedDevice_nil": @(atomic_load(&nilsSharedDevice)),
            @"sharedCommandQueue": @(atomic_load(&callsSharedQueue)),
            @"sharedCommandQueue_nil": @(atomic_load(&nilsSharedQueue)),
            @"sharedNilTexture2D": @(atomic_load(&callsNilTexture)),
            @"nullAtlas2D": @(atomic_load(&callsNullAtlas)),
            @"snapshotsByDefinition": @(atomic_load(&callsSnapshots)),
            @"snapshotImagesByDefinition": @(atomic_load(&callsSnapshotImages)),
            @"didTakeSnapshots": @(atomic_load(&callsDidTake)),
            @"failedWithError": @(atomic_load(&callsFailed)),
            @"hooks_installed": @(atomic_load(&installed))
        }
    };
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:&error];
    if (!json || ![json writeToFile:receiptPath options:NSDataWritingAtomic error:&error])
        syslog(3, "NEZHA Snapshot: receipt write failed: %s", error.description.UTF8String ?: "unknown");
}

/* Rewrite the same receipt periodically: a bounded RAM boot may end without a
 * clean process teardown, so the last successful write is the evidence. */
static void scheduleReceipt(unsigned seconds, unsigned repeats)
{
    if (!repeats) return;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)seconds * NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        writeReceipt();
        scheduleReceipt(seconds, repeats - 1);
    });
}

static void setupReceipt(const char *process)
{
    observationLock = [NSLock new];
    observationEvents = [NSMutableArray array];
    observerProcess = @(process ?: "unknown");
    char boot[128] = {};
    size_t size = sizeof boot - 1;
    if (sysctlbyname("kern.bootsessionuuid", boot, &size, NULL, 0)) {
        bootIdentifier = @"no-bootsessionuuid";
    } else {
        bootIdentifier = @(boot);
    }
    NSString *directory = [@"/private/var/mobile/Library/Logs/NezhaSnapshotObservation"
                           stringByAppendingPathComponent:bootIdentifier];
    NSError *error = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:directory
                                  withIntermediateDirectories:YES attributes:nil error:&error]) {
        syslog(3, "NEZHA Snapshot: receipt directory failed: %s", error.description.UTF8String ?: "unknown");
        return;
    }
    receiptPath = [directory stringByAppendingPathComponent:
                   [NSString stringWithFormat:@"receipt-%@.json", observerProcess]];
}

/* Mirrors the established shim pattern (metal_texture_layout.m): the original
 * IMP is cast to the exact fixed-arity function pointer at the call site. On
 * arm64 both forms share the same calling convention. */
static IMP replaceMethod(Class cls, BOOL isClassMethod, SEL selector, IMP replacement)
{
    Method method = isClassMethod ? class_getClassMethod(cls, selector)
                                 : class_getInstanceMethod(cls, selector);
    if (!method) return NULL;
    return method_setImplementation(method, replacement);
}

/* ---- ClockKit Metal resource manager: is a real device/queue available? ---- */

static id (*originalSharedDevice)(id, SEL);
static id observedSharedDevice(id self, SEL selector)
{
    id device = originalSharedDevice(self, selector);
    unsigned index = atomic_fetch_add(&callsSharedDevice, 1);
    if (!device) atomic_fetch_add(&nilsSharedDevice, 1);
    if (index < 4) SNAPLOG(@"sharedDevice #%u -> %s", index, classNameOf(device));
    return device;
}

static id (*originalSharedQueue)(id, SEL);
static id observedSharedQueue(id self, SEL selector)
{
    id queue = originalSharedQueue(self, selector);
    unsigned index = atomic_fetch_add(&callsSharedQueue, 1);
    if (!queue) atomic_fetch_add(&nilsSharedQueue, 1);
    if (index < 4) SNAPLOG(@"sharedCommandQueue #%u -> %s", index, classNameOf(queue));
    return queue;
}

static id (*originalNilTexture)(id, SEL);
static id observedNilTexture(id self, SEL selector)
{
    atomic_fetch_add(&callsNilTexture, 1);
    return originalNilTexture(self, selector);
}

static id (*originalNullAtlas)(id, SEL);
static id observedNullAtlas(id self, SEL selector)
{
    id atlas = originalNullAtlas(self, selector);
    unsigned index = atomic_fetch_add(&callsNullAtlas, 1);
    if (index < 4) SNAPLOG(@"nullAtlas2D #%u -> %s", index, classNameOf(atlas));
    return atlas;
}

/* ---- PosterBoard snapshotter: what is actually produced? ---- */

static id (*originalSnapshots)(id, SEL);
static id observedSnapshots(id self, SEL selector)
{
    id result = originalSnapshots(self, selector);
    unsigned index = atomic_fetch_add(&callsSnapshots, 1);
    if (index < 6) {
        NSUInteger count = 0;
        if ([result isKindOfClass:[NSDictionary class]]) count = [(NSDictionary *)result count];
        else if ([result isKindOfClass:[NSArray class]]) count = [(NSArray *)result count];
        SNAPLOG(@"snapshotsByDefinition #%u -> %s count=%lu", index,
                classNameOf(result), (unsigned long)count);
    }
    return result;
}

static id (*originalSnapshotImages)(id, SEL);
static id observedSnapshotImages(id self, SEL selector)
{
    id result = originalSnapshotImages(self, selector);
    unsigned index = atomic_fetch_add(&callsSnapshotImages, 1);
    if (index < 6) {
        NSUInteger count = 0;
        if ([result isKindOfClass:[NSDictionary class]]) count = [(NSDictionary *)result count];
        else if ([result isKindOfClass:[NSArray class]]) count = [(NSArray *)result count];
        SNAPLOG(@"snapshotImagesByDefinition #%u -> %s count=%lu", index,
                classNameOf(result), (unsigned long)count);
    }
    return result;
}

/* Both operation selectors take two object arguments; observing them as id
 * keeps the ABI independent of the private headers. */
static void (*originalDidTake)(id, SEL, id, id);
static void observedDidTake(id self, SEL selector, id snapshots, id byDefinition)
{
    unsigned index = atomic_fetch_add(&callsDidTake, 1);
    if (index < 6) {
        NSUInteger count = 0;
        if ([byDefinition isKindOfClass:[NSDictionary class]]) count = [(NSDictionary *)byDefinition count];
        SNAPLOG(@"didTakeSnapshots #%u snapshots=%s byDefinition=%s count=%lu", index,
                classNameOf(snapshots), classNameOf(byDefinition), (unsigned long)count);
    }
    originalDidTake(self, selector, snapshots, byDefinition);
}

static void (*originalFailed)(id, SEL, id, id);
static void observedFailed(id self, SEL selector, id snapshotter, id error)
{
    unsigned index = atomic_fetch_add(&callsFailed, 1);
    if (index < 6) SNAPLOG(@"failedWithError #%u error=%s", index, classNameOf(error));
    originalFailed(self, selector, snapshotter, error);
}

/* PosterKit / ClockKitUI may be dlopen'd after our constructor runs. Retry
 * once per second on a utility queue instead of assuming load order. */
static BOOL installHooks(void)
{
    if (atomic_load(&installed)) return YES;
    Class metal = objc_getClass("CLKUIMetalResourceManager");
    Class snapshotter = objc_getClass("PBFPosterSnapshotter");
    Class operation = objc_getClass("PBFPosterSnapshotterOperation");
    if (!metal && !snapshotter && !operation) return NO;

    SNAPLOG(@"classes metal=%s snapshotter=%s operation=%s pid=%d",
            metal ? class_getName(metal) : "absent",
            snapshotter ? class_getName(snapshotter) : "absent",
            operation ? class_getName(operation) : "absent", getpid());

    unsigned hooked = 0;
    IMP saved;
    if (metal) {
        saved = replaceMethod(metal, YES, sel_registerName("sharedDevice"), (IMP)observedSharedDevice);
        if (saved) { originalSharedDevice = (id (*)(id, SEL))saved; hooked++; }
        saved = replaceMethod(metal, YES, sel_registerName("sharedCommandQueue"), (IMP)observedSharedQueue);
        if (saved) { originalSharedQueue = (id (*)(id, SEL))saved; hooked++; }
        saved = replaceMethod(metal, YES, sel_registerName("sharedNilTexture2D"), (IMP)observedNilTexture);
        if (saved) { originalNilTexture = (id (*)(id, SEL))saved; hooked++; }
        saved = replaceMethod(metal, NO, sel_registerName("nullAtlas2D"), (IMP)observedNullAtlas);
        if (saved) { originalNullAtlas = (id (*)(id, SEL))saved; hooked++; }
    }
    if (snapshotter) {
        saved = replaceMethod(snapshotter, NO, sel_registerName("snapshotsByDefinition"), (IMP)observedSnapshots);
        if (saved) { originalSnapshots = (id (*)(id, SEL))saved; hooked++; }
        saved = replaceMethod(snapshotter, NO, sel_registerName("snapshotImagesByDefinition"), (IMP)observedSnapshotImages);
        if (saved) { originalSnapshotImages = (id (*)(id, SEL))saved; hooked++; }
    }
    if (operation) {
        saved = replaceMethod(operation, NO,
                              sel_registerName("snapshotterDidTakeSnapshots:snapshotsByDefinition:"),
                              (IMP)observedDidTake);
        if (saved) { originalDidTake = (void (*)(id, SEL, id, id))saved; hooked++; }
        saved = replaceMethod(operation, NO, sel_registerName("snapshotter:failedWithError:"),
                              (IMP)observedFailed);
        if (saved) { originalFailed = (void (*)(id, SEL, id, id))saved; hooked++; }
    }
    SNAPLOG(@"installed %u of 8 hooks", hooked);
    atomic_store(&installed, 1);
    writeReceipt();
    return YES;
}

static void scheduleInstall(unsigned remaining)
{
    if (installHooks()) return;
    if (!remaining) {
        SNAPLOG(@"no snapshotter classes appeared before the observation window ended");
        writeReceipt();
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        scheduleInstall(remaining - 1);
    });
}

__attribute__((constructor)) static void beginSnapshotObservation(void)
{
    @autoreleasepool {
        const char *process = getprogname();
        setupReceipt(process);
        /* Every loaded process leaves a receipt so the shim's injection scope
         * is directly observable; only PosterBoard owns the private classes. */
        if (strcmp(process, "PosterBoard")) {
            SNAPLOG(@"observer loaded without snapshotter hooks in %s", process);
            scheduleReceipt(10, 8);
            return;
        }
        SNAPLOG(@"observer starting in PosterBoard pid=%d", getpid());
        scheduleInstall(60);
        scheduleReceipt(10, 8);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 75LL * NSEC_PER_SEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            SNAPLOG(@"final summary");
            writeReceipt();
        });
    }
}
