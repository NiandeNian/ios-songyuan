/* One-shot repair of derived astronomy gallery snapshots. Included only in an
 * explicit repair shim, and activated only for PosterBoard by a boot argument.
 * The plan names exact files inspected on this device. Atomic rename preserves
 * the original file and permissions; an existing backup makes the action a no-op.
 * No configuration, media asset, descriptor metadata or other wallpaper is edited. */
#import <Foundation/Foundation.h>
#include <sys/sysctl.h>
#include <sys/stat.h>
#include <sys/stdio.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include "astronomy_refresh_plan.h"

static BOOL enabled(void)
{
    char arguments[2048] = {}; size_t size = sizeof arguments - 1;
    if (sysctlbyname("kern.bootargs", arguments, &size, NULL, 0)) return NO;
    static const char token[] = "nezha_thumbnail_refresh=1";
    const char *at = strstr(arguments, token);
    return at && (at == arguments || at[-1] == ' ') &&
           (at[sizeof token - 1] == 0 || at[sizeof token - 1] == ' ');
}

static BOOL derivedGalleryPath(NSString *path)
{
    NSString *prefix = @"/private/var/mobile/Library/Application Support/PRBPosterExtensionDataStore/";
    return [path isKindOfClass:NSString.class] && [path hasPrefix:prefix] &&
        [path containsString:@"/Extensions/com.apple.NanoUniverse.AegirProxyApp.AegirPoster/staticdescriptors/"] &&
        [path containsString:@"/versions/"] && [path.lastPathComponent hasPrefix:@"SNAPSHOT_GALLERY-"] &&
        [path hasSuffix:@".atx"] && ![path.pathComponents containsObject:@".."];
}

__attribute__((constructor)) static void refreshAstronomy(void)
{
    if (strcmp(getprogname(), "PosterBoard") || !enabled()) return;
    @autoreleasepool {
        NSError *error = nil;
        NSArray *plan = [NSJSONSerialization JSONObjectWithData:[NSData dataWithBytes:astronomy_refresh_plan
            length:sizeof astronomy_refresh_plan] options:0 error:&error];
        if (![plan isKindOfClass:NSArray.class] || !plan.count || plan.count > 8) {
            NSLog(@"NEZHA thumbnail refresh: invalid plan: %@", error); return;
        }
        char boot[128] = {}; size_t size = sizeof boot;
        if (sysctlbyname("kern.bootsessionuuid", boot, &size, NULL, 0)) return;
        NSString *directory = [@"/private/var/mobile/Library/Logs/NezhaAstronomyThumbnailRefresh" stringByAppendingPathComponent:@(boot)];
        if (![[NSFileManager defaultManager] createDirectoryAtPath:directory withIntermediateDirectories:YES attributes:nil error:&error]) {
            NSLog(@"NEZHA thumbnail refresh: receipt directory failed: %@", error); return;
        }
        NSMutableArray *operations = [NSMutableArray array];
        for (NSString *source in plan) {
            if (!derivedGalleryPath(source)) { NSLog(@"NEZHA thumbnail refresh: refused path %@", source); return; }
        }
        for (NSString *source in plan) {
            NSString *backup = [source stringByAppendingString:@".nezha-before-thumbnail-refresh-20261104"];
            NSMutableDictionary *operation = [@{@"source":source, @"backup":backup} mutableCopy];
            struct stat attributes;
            if (!lstat(backup.fileSystemRepresentation, &attributes)) {
                operation[@"result"] = @"backup-already-present";
            } else if (errno != ENOENT) {
                operation[@"result"] = @"backup-stat-failed"; operation[@"errno"] = @(errno);
            } else {
                int file = open(source.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW);
                if (file < 0) {
                    operation[@"result"] = errno == ENOENT ? @"source-missing" : @"open-failed";
                    operation[@"errno"] = @(errno);
                } else {
                    unsigned char header[64] = {};
                    ssize_t bytes = pread(file, header, sizeof header, 0x4000);
                    int readError = errno, closed = close(file);
                    uint64_t width = 0, height = 0;
                    memcpy(&width, header + 16, sizeof width); memcpy(&height, header + 24, sizeof height);
                    if (bytes != sizeof header || closed || memcmp(header, "UniXNU ASTC v1", 14) || width != 302 || height != 656) {
                        operation[@"result"] = @"layout-check-failed"; operation[@"read_errno"] = @(readError);
                    } else if (renamex_np(source.fileSystemRepresentation, backup.fileSystemRepresentation, RENAME_EXCL)) {
                        operation[@"result"] = @"rename-failed"; operation[@"errno"] = @(errno);
                    } else {
                        operation[@"result"] = @"original-backed-up";
                    }
                }
            }
            [operations addObject:operation];
            NSLog(@"NEZHA thumbnail refresh: %@ %@", operation[@"result"], source);
        }
        NSDictionary *receipt = @{@"boot":@(boot), @"operations":operations, @"regenerated_pixels_verified":@NO};
        NSData *json = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingPrettyPrinted error:&error];
        if (!json || ![json writeToFile:[directory stringByAppendingPathComponent:@"receipt.json"] options:NSDataWritingAtomic error:&error])
            NSLog(@"NEZHA thumbnail refresh: receipt write failed: %@", error);
    }
}
