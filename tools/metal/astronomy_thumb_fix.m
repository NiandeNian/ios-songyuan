/* Self-healing repair of black astronomy gallery thumbnails.
 *
 * The snapshotter's offscreen capture cannot record CAMetalLayer content, so
 * gallery thumbnails for Metal-rendered wallpapers are regenerated as pure
 * black on every boot.  This shim copies a known-good .atx over every black
 * gallery thumbnail it finds in the AegirPoster staticdescriptors directory.
 * It runs a bounded periodic check (every 5 s for 3 minutes) to catch
 * snapshotter regeneration.
 *
 * The good .atx lives at /private/var/mobile/Library/Logs/astro-good.atx
 * (installed by the firmware container into an already-existing directory).
 *
 * Workaround, not fix: the underlying capture defect remains.
 * Opt-in build (--astronomy-thumb-fix); normal builds omit this file.
 *
 * Constructor must be minimal: the dylib is injected into every process via
 * DYLD_INSERT_LIBRARIES and constructors run before the ObjC runtime is
 * fully warmed up.  A previous version with @autoreleasepool + ARC statics
 * in the constructor crashed SpringBoard with pc=0.  All ObjC work happens
 * inside dispatch blocks after a delay.
 */
#import <Foundation/Foundation.h>
#include <dispatch/dispatch.h>
#include <stdatomic.h>
#include <string.h>
#include <syslog.h>
#include <unistd.h>

static const unsigned char kConstBlack[16] = {
    0xfc,0xfd,0xff,0xff,0xff,0xff,0xff,0xff,
    0x00,0x00,0x00,0x00,0x00,0x00,0xff,0xff
};

static BOOL looksBlack(NSData *d)
{
    if (d.length < 0x4050) return NO;
    const unsigned char *b = d.bytes;
    int black = 0;
    int max = (int)((d.length - 0x4040) / 16);
    for (int i = 0; i < 3 && i < max; i++)
        if (memcmp(b + 0x4040 + i * 16, kConstBlack, 16) == 0) black++;
    return black >= 2;
}

static void repairOnce(void)
{
    @autoreleasepool {
        NSData *good = [NSData dataWithContentsOfFile:
                        @"/private/var/mobile/Library/Logs/astro-good.atx"];
        if (!good.length) { syslog(5, "NEZHA AstroFix: good.atx missing"); return; }

        NSString *store = @"/private/var/mobile/Library/Application Support/PRBPosterExtensionDataStore";
        NSDirectoryEnumerator *en = [[NSFileManager defaultManager] enumeratorAtPath:store];
        NSString *rel;
        unsigned n = 0;
        while ((rel = [en nextObject])) {
            if (![rel hasSuffix:@".atx"]) continue;
            if (![rel containsString:@"AegirPoster"]) continue;
            if (![rel containsString:@"staticdescriptors"]) continue;
            if (![rel containsString:@"SNAPSHOT_GALLERY"]) continue;
            if ([rel containsString:@"HERO"]) continue;
            NSString *full = [store stringByAppendingPathComponent:rel];
            NSData *cur = [NSData dataWithContentsOfFile:full options:NSDataReadingMappedIfSafe error:NULL];
            if (looksBlack(cur)) {
                if ([good writeToFile:full options:NSDataWritingAtomic error:NULL]) {
                    n++;
                    syslog(5, "NEZHA AstroFix: replaced %s", rel.lastPathComponent.UTF8String);
                }
            }
        }
        syslog(5, "NEZHA AstroFix: pass done, replaced %u", n);
    }
}

__attribute__((constructor)) static void begin(void)
{
    if (strcmp(getprogname(), "PosterBoard")) return;
    for (int i = 1; i <= 36; i++) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(5 + 5 * i) * NSEC_PER_SEC),
                       dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
            repairOnce();
        });
    }
}
