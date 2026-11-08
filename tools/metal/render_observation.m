/* Observation of the wallpaper gallery snapshot render/capture pipeline.
 *
 * Why this exists: the snapshot observation shim (snapshot_observation.m)
 * proved that ClockKit's Metal resource manager is never called during
 * snapshot generation (sharedDevice=0, nullAtlas2D=0), so the "null atlas
 * fallback" hypothesis is disproven.  The remaining question is HOW the
 * snapshotter captures the poster's visual content.  Two candidate paths:
 *
 *   1. -[CALayer renderInContext:]  — renders into a CGContext; does NOT
 *      capture CAMetalLayer / Metal drawable content.  If this is the
 *      capture method, Metal-rendered wallpapers produce black thumbnails.
 *   2. -[UIView drawViewHierarchyInRect:afterScreenUpdates:]  — goes
 *      through the render server and CAN capture Metal layer content.
 *
 * This instrument hooks both (plus CAMetalLayer drawable activity and the
 * snapshotter's cache/populate lifecycle) to determine which path is used.
 * It never changes an argument, return value, asset, or wallpaper
 * configuration.  Linked only into an explicit opt-in diagnostic shim
 * (--render-observation); an ordinary NezhaStatusBar build does not contain
 * this translation unit.
 *
 * Readback is a file receipt (same convention as snapshot_observation.m:
 * PosterBoard NSLog does not reach the pinned kernel log).  Receipts live
 * in /private/var/mobile/Library/Logs/NezhaRenderObservation/<boot-uuid>/.
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
static atomic_uint callsRenderInContext, callsDrawHierarchy;
static atomic_uint callsNextDrawable, callsMainStart, callsAssetCatalog, callsDiskCache;
static atomic_uint snapshotterActive;
static atomic_int installed;

static NSLock *observationLock;
static NSMutableArray *observationEvents;
static NSString *receiptPath;
static NSString *observerProcess;
static NSString *bootIdentifier;

static void recordEvent(NSString *line)
{
    [observationLock lock];
    if (observationEvents.count < 120) [observationEvents addObject:line];
    [observationLock unlock];
    syslog(5, "%s", line.UTF8String);
}

#define RLOG(fmt, ...) recordEvent([NSString stringWithFormat:@"NEZHA Render: " fmt, ##__VA_ARGS__])

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
            @"renderInContext": @(atomic_load(&callsRenderInContext)),
            @"drawViewHierarchyInRect": @(atomic_load(&callsDrawHierarchy)),
            @"nextDrawable": @(atomic_load(&callsNextDrawable)),
            @"mainStart": @(atomic_load(&callsMainStart)),
            @"assetCatalogPopulate": @(atomic_load(&callsAssetCatalog)),
            @"diskCachePopulate": @(atomic_load(&callsDiskCache)),
            @"snapshotterActive": @(atomic_load(&snapshotterActive)),
            @"hooks_installed": @(atomic_load(&installed))
        }
    };
    NSError *error = nil;
    NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:&error];
    if (!json || ![json writeToFile:receiptPath options:NSDataWritingAtomic error:&error])
        syslog(3, "NEZHA Render: receipt write failed: %s", error.description.UTF8String ?: "unknown");
}

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
    NSString *directory = [@"/private/var/mobile/Library/Logs/NezhaRenderObservation"
                           stringByAppendingPathComponent:bootIdentifier];
    NSError *error = nil;
    if (![[NSFileManager defaultManager] createDirectoryAtPath:directory
                                  withIntermediateDirectories:YES attributes:nil error:&error]) {
        syslog(3, "NEZHA Render: receipt directory failed: %s", error.description.UTF8String ?: "unknown");
        return;
    }
    receiptPath = [directory stringByAppendingPathComponent:
                   [NSString stringWithFormat:@"receipt-%@.json", observerProcess]];
}

static IMP replaceMethod(Class cls, BOOL isClassMethod, SEL selector, IMP replacement)
{
    Method method = isClassMethod ? class_getClassMethod(cls, selector)
                                 : class_getInstanceMethod(cls, selector);
    if (!method) return NULL;
    return method_setImplementation(method, replacement);
}

/* ---- CALayer renderInContext: the smoking gun for Metal content loss ---- */

static void (*originalRenderInContext)(id, SEL, void *);
static void observedRenderInContext(id self, SEL selector, void *ctx)
{
    unsigned index = atomic_fetch_add(&callsRenderInContext, 1);
    /* Log only the first few calls and any call while the snapshotter is
     * active, to keep the receipt bounded while capturing the snapshot path. */
    if (index < 6 || atomic_load(&snapshotterActive)) {
        /* Read the layer's bounds via KVC to avoid requiring UIKit headers. */
        id boundsVal = nil;
        @try { boundsVal = [self valueForKey:@"bounds"]; } @catch (id) {}
        RLOG(@"renderInContext #%u layer=%s bounds=%s ctx=%p active=%d",
             index, classNameOf(self),
             boundsVal ? [[boundsVal description] UTF8String] : "?",
             ctx, atomic_load(&snapshotterActive));
    }
    originalRenderInContext(self, selector, ctx);
}

/* ---- UIView drawViewHierarchyInRect:afterScreenUpdates: ---- */

static BOOL (*originalDrawHierarchy)(id, SEL, void *, BOOL);
static BOOL observedDrawHierarchy(id self, SEL selector, void *rect, BOOL afterUpdates)
{
    unsigned index = atomic_fetch_add(&callsDrawHierarchy, 1);
    if (index < 6 || atomic_load(&snapshotterActive)) {
        RLOG(@"drawViewHierarchy #%u view=%s after=%d active=%d",
             index, classNameOf(self), afterUpdates, atomic_load(&snapshotterActive));
    }
    return originalDrawHierarchy(self, selector, rect, afterUpdates);
}

/* ---- CAMetalLayer nextDrawable: Metal activity during snapshot ---- */

static id (*originalNextDrawable)(id, SEL);
static id observedNextDrawable(id self, SEL selector)
{
    atomic_fetch_add(&callsNextDrawable, 1);
    return originalNextDrawable(self, selector);
}

/* ---- PBFPosterSnapshotter lifecycle ---- */

static void (*originalMainStart)(id, SEL);
static void observedMainStart(id self, SEL selector)
{
    atomic_fetch_add(&callsMainStart, 1);
    atomic_store(&snapshotterActive, 1);
    RLOG(@"_main_start #%u snapshotter=%s", atomic_load(&callsMainStart), classNameOf(self));
    originalMainStart(self, selector);
    atomic_store(&snapshotterActive, 0);
    RLOG(@"_main_start #%u finished", atomic_load(&callsMainStart));
}

static id (*originalAssetCatalog)(id, SEL);
static id observedAssetCatalog(id self, SEL selector)
{
    unsigned index = atomic_fetch_add(&callsAssetCatalog, 1);
    if (index < 8) RLOG(@"_lock_populateSnapshotsFromAssetCatalogIfPossible #%u", index);
    return originalAssetCatalog(self, selector);
}

static id (*originalDiskCache)(id, SEL);
static id observedDiskCache(id self, SEL selector)
{
    unsigned index = atomic_fetch_add(&callsDiskCache, 1);
    if (index < 8) RLOG(@"_lock_populateSnapshotsFromDiskIfPossible #%u", index);
    return originalDiskCache(self, selector);
}

/* ---- Hook installation (retry for late-loaded frameworks) ---- */

static BOOL installHooks(void)
{
    if (atomic_load(&installed)) return YES;
    Class calayer = objc_getClass("CALayer");
    Class uiview = objc_getClass("UIView");
    Class metalLayer = objc_getClass("CAMetalLayer");
    Class snapshotter = objc_getClass("PBFPosterSnapshotter");
    if (!calayer && !uiview && !metalLayer && !snapshotter) return NO;

    RLOG(@"classes calayer=%s uiview=%s metalLayer=%s snapshotter=%s pid=%d",
         calayer ? class_getName(calayer) : "absent",
         uiview ? class_getName(uiview) : "absent",
         metalLayer ? class_getName(metalLayer) : "absent",
         snapshotter ? class_getName(snapshotter) : "absent", getpid());

    unsigned hooked = 0;
    IMP saved;
    if (calayer) {
        saved = replaceMethod(calayer, NO, sel_registerName("renderInContext:"), (IMP)observedRenderInContext);
        if (saved) { originalRenderInContext = (void (*)(id, SEL, void *))saved; hooked++; }
    }
    if (uiview) {
        saved = replaceMethod(uiview, NO,
                              sel_registerName("drawViewHierarchyInRect:afterScreenUpdates:"),
                              (IMP)observedDrawHierarchy);
        if (saved) { originalDrawHierarchy = (BOOL (*)(id, SEL, void *, BOOL))saved; hooked++; }
    }
    if (metalLayer) {
        saved = replaceMethod(metalLayer, NO, sel_registerName("nextDrawable"), (IMP)observedNextDrawable);
        if (saved) { originalNextDrawable = (id (*)(id, SEL))saved; hooked++; }
    }
    if (snapshotter) {
        saved = replaceMethod(snapshotter, NO, sel_registerName("_main_start"), (IMP)observedMainStart);
        if (saved) { originalMainStart = (void (*)(id, SEL))saved; hooked++; }
        saved = replaceMethod(snapshotter, NO,
                              sel_registerName("_lock_populateSnapshotsFromAssetCatalogIfPossible"),
                              (IMP)observedAssetCatalog);
        if (saved) { originalAssetCatalog = (id (*)(id, SEL))saved; hooked++; }
        saved = replaceMethod(snapshotter, NO,
                              sel_registerName("_lock_populateSnapshotsFromDiskIfPossible"),
                              (IMP)observedDiskCache);
        if (saved) { originalDiskCache = (id (*)(id, SEL))saved; hooked++; }
    }
    RLOG(@"installed %u of 7 hooks", hooked);
    atomic_store(&installed, 1);
    writeReceipt();
    return YES;
}

static void scheduleInstall(unsigned remaining)
{
    if (installHooks()) return;
    if (!remaining) {
        RLOG(@"no render observation classes appeared before the window ended");
        writeReceipt();
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)NSEC_PER_SEC),
                   dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        scheduleInstall(remaining - 1);
    });
}

__attribute__((constructor)) static void beginRenderObservation(void)
{
    @autoreleasepool {
        const char *process = getprogname();
        setupReceipt(process);
        if (strcmp(process, "PosterBoard")) {
            RLOG(@"observer loaded without render hooks in %s", process);
            scheduleReceipt(10, 8);
            return;
        }
        RLOG(@"render observer starting in PosterBoard pid=%d", getpid());
        scheduleInstall(60);
        scheduleReceipt(10, 8);
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 75LL * NSEC_PER_SEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            RLOG(@"final summary");
            writeReceipt();
        });
    }
}
