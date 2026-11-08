/* nezha-atx-repair: automatic repair of black gallery/switcher thumbnails.
 *
 * The snapshotter's offscreen capture cannot record CAMetalLayer content, so
 * .atx thumbnails for Metal-rendered wallpapers are generated as pure black.
 * RuntimeSnapshot .atx files (captured at display time) have correct content.
 *
 * This standalone binary scans PRBPosterExtensionDataStore for black .atx
 * files and replaces them from the corresponding RuntimeSnapshot in the same
 * configuration directory.  It runs as a launchd service, independent of any
 * dylib injection, so it cannot crash SpringBoard.
 *
 * Usage: nezha-atx-repair [--once] [--verbose]
 *   --once    run a single pass and exit (for testing)
 *   --verbose log every action
 *
 * Default mode: run one pass at startup, then every 60 s for 5 minutes
 * (to catch snapshotter regeneration), then exit.
 *
 * Build: clang -target arm64-apple-ios16.0 -isysroot $SDK -O2 -Wall -Wextra
 *        -Werror nezha-atx-repair.c -o nezha-atx-repair
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <unistd.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/sysctl.h>

#define BLOCK 4096
#define HEADER_OFF 0x4000
#define PAYLOAD_OFF 0x4040
#define MAX_PATH 1024

static const unsigned char CONST_PREFIX[8] = {
    0xfc,0xfd,0xff,0xff,0xff,0xff,0xff,0xff
};
static const unsigned char CONST_BLACK[16] = {
    0xfc,0xfd,0xff,0xff,0xff,0xff,0xff,0xff,
    0x00,0x00,0x00,0x00,0x00,0x00,0xff,0xff
};

static int verbose = 0;
static int fixed_count = 0;
static int checked_count = 0;

static void logmsg(const char *fmt, ...)
{
    if (!verbose) return;
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\n");
}

/* Read a file entirely into a malloc'd buffer.  Returns NULL on failure. */
static unsigned char *read_file(const char *path, size_t *out_len)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return NULL;
    struct stat st;
    if (fstat(fd, &st) || st.st_size <= 0) { close(fd); return NULL; }
    unsigned char *buf = malloc(st.st_size);
    if (!buf) { close(fd); return NULL; }
    size_t got = 0;
    while (got < (size_t)st.st_size) {
        ssize_t n = read(fd, buf + got, st.st_size - got);
        if (n <= 0) { free(buf); close(fd); return NULL; }
        got += n;
    }
    close(fd);
    *out_len = got;
    return buf;
}

static int write_file(const char *path, const unsigned char *data, size_t len)
{
    int fd = open(path, O_WRONLY | O_TRUNC);
    if (fd < 0) return -1;
    size_t put = 0;
    while (put < len) {
        ssize_t n = write(fd, data + put, len - put);
        if (n <= 0) { close(fd); return -1; }
        put += n;
    }
    close(fd);
    return 0;
}

/* Check if the first 3 payload blocks are constant-black. */
static int is_black(const unsigned char *data, size_t len)
{
    if (len < PAYLOAD_OFF + 48) return 0;
    int black = 0;
    for (int i = 0; i < 3; i++)
        if (memcmp(data + PAYLOAD_OFF + i * 16, CONST_BLACK, 16) == 0) black++;
    return black >= 2;
}

/* Parse layout header at offset 0x4000.  Returns 0 on success. */
static int parse_layout(const unsigned char *data, size_t len,
                        uint32_t *w, uint32_t *h, uint64_t *payload)
{
    if (len < PAYLOAD_OFF) return -1;
    if (memcmp(data + HEADER_OFF, "UniXNU ASTC v1", 14) != 0) return -1;
    /* width, height at +16 (uint64 LE) */
    uint64_t w64, h64, p64;
    memcpy(&w64, data + HEADER_OFF + 16, 8);
    memcpy(&h64, data + HEADER_OFF + 24, 8);
    memcpy(&p64, data + HEADER_OFF + 40, 8);
    *w = (uint32_t)w64; *h = (uint32_t)h64; *payload = p64;
    return 0;
}

/* Block-sample downscale: for each target block, pick corresponding source block. */
static unsigned char *downscale(const unsigned char *src, size_t src_len,
                                uint32_t tw, uint32_t th, uint64_t tpayload,
                                uint32_t tbw, uint32_t tbh, size_t *out_len)
{
    uint32_t sw, sh; uint64_t spayload;
    if (parse_layout(src, src_len, &sw, &sh, &spayload)) return NULL;
    uint32_t sbw = 4, sbh = 4; /* assume 4x4 */
    uint32_t scols = (sw + sbw - 1) / sbw;
    uint32_t srows = (sh + sbh - 1) / sbh;
    uint32_t tcols = (tw + tbw - 1) / tbw;
    uint32_t trows = (th + tbh - 1) / tbh;

    size_t out_size = tpayload + PAYLOAD_OFF;
    unsigned char *out = calloc(1, out_size);
    if (!out) return NULL;

    /* Copy header, update dimensions */
    memcpy(out, src, PAYLOAD_OFF > src_len ? src_len : PAYLOAD_OFF);
    /* Fix layout header at 0x4000 */
    uint64_t tw64 = tw, th64 = th, one = 1, tp = tpayload;
    memcpy(out + HEADER_OFF + 16, &tw64, 8);
    memcpy(out + HEADER_OFF + 24, &th64, 8);
    memcpy(out + HEADER_OFF + 32, &one, 8);
    memcpy(out + HEADER_OFF + 40, &tp, 8);
    /* Fix AAPL width/height at 0x28 */
    uint32_t tw32 = tw, th32 = th;
    memcpy(out + 0x28, &tw32, 4);
    memcpy(out + 0x2c, &th32, 4);

    /* Generate payload by block sampling */
    for (uint32_t trow = 0; trow < trows; trow++) {
        for (uint32_t tcol = 0; tcol < tcols; tcol++) {
            uint32_t srow = (uint32_t)((uint64_t)trow * srows / trows);
            uint32_t scol = (uint32_t)((uint64_t)tcol * scols / tcols);
            if (srow >= srows) srow = srows - 1;
            if (scol >= scols) scol = scols - 1;
            size_t idx = (size_t)srow * scols + scol;
            const unsigned char *sblk = src + PAYLOAD_OFF + idx * 16;
            unsigned char *tblk = out + PAYLOAD_OFF + ((size_t)trow * tcols + tcol) * 16;
            /* If source block is constant-color, use its color; else dark blue */
            if (memcmp(sblk, CONST_PREFIX, 8) == 0) {
                memcpy(tblk, sblk, 16);
            } else {
                memcpy(tblk, CONST_PREFIX, 8);
                /* RGBA = dark blue */
                tblk[8] = 0; tblk[9] = 0x28;
                tblk[10] = 0; tblk[11] = 0x50;
                tblk[12] = 0; tblk[13] = 0xa0;
                tblk[14] = 0xff; tblk[15] = 0xff;
            }
        }
    }

    *out_len = out_size;
    return out;
}

/* Recursively scan for .atx files under `dir`. */
static void scan_dir(const char *dir, int depth)
{
    if (depth > 10) return;
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *ent;
    char path[MAX_PATH];
    while ((ent = readdir(d))) {
        if (ent->d_name[0] == '.') continue;
        snprintf(path, sizeof path, "%s/%s", dir, ent->d_name);
        struct stat st;
        if (lstat(path, &st)) continue;
        if (S_ISDIR(st.st_mode)) {
            scan_dir(path, depth + 1);
        } else if (S_ISREG(st.st_mode)) {
            size_t nlen = strlen(ent->d_name);
            if (nlen < 4 || strcmp(ent->d_name + nlen - 4, ".atx")) continue;
            checked_count++;

            size_t len;
            unsigned char *data = read_file(path, &len);
            if (!data) continue;
            if (!is_black(data, len)) { free(data); continue; }

            /* Found a black .atx.  Find RuntimeSnapshot in same directory. */
            char dirpath[MAX_PATH];
            strncpy(dirpath, path, sizeof dirpath - 1);
            dirpath[sizeof dirpath - 1] = 0;
            char *last_slash = strrchr(dirpath, '/');
            if (last_slash) *last_slash = 0;

            /* Look for RuntimeSnapshot-*.atx in the same directory */
            DIR *dd = opendir(dirpath);
            char src_path[MAX_PATH] = {0};
            if (dd) {
                struct dirent *e2;
                while ((e2 = readdir(dd))) {
                    if (strncmp(e2->d_name, "RuntimeSnapshot", 15) == 0) {
                        snprintf(src_path, sizeof src_path, "%s/%s", dirpath, e2->d_name);
                        break;
                    }
                }
                closedir(dd);
            }

            if (!src_path[0]) {
                logmsg("no RuntimeSnapshot for %s", path);
                free(data);
                continue;
            }

            size_t src_len;
            unsigned char *src = read_file(src_path, &src_len);
            if (!src) { free(data); continue; }

            int repaired = 0;
            if (src_len == len) {
                /* Same size: direct copy */
                if (write_file(path, src, src_len) == 0) {
                    repaired = 1;
                    logmsg("copied %s <- %s", path, src_path);
                }
            } else {
                /* Different size: downscale */
                uint32_t tw, th; uint64_t tpayload;
                if (parse_layout(data, len, &tw, &th, &tpayload) == 0) {
                    size_t out_len;
                    unsigned char *out = downscale(src, src_len, tw, th, tpayload, 4, 4, &out_len);
                    if (out) {
                        if (write_file(path, out, out_len) == 0) {
                            repaired = 1;
                            logmsg("downscaled %s <- %s", path, src_path);
                        }
                        free(out);
                    }
                }
            }

            if (repaired) fixed_count++;
            free(src);
            free(data);
        }
    }
    closedir(d);
}

int main(int argc, char **argv)
{
    int once = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--once") == 0) once = 1;
        if (strcmp(argv[i], "--verbose") == 0) verbose = 1;
    }

    const char *root = "/private/var/mobile/Library/Application Support/PRBPosterExtensionDataStore";

    if (once) {
        scan_dir(root, 0);
        fprintf(stderr, "nezha-atx-repair: checked=%d fixed=%d\n", checked_count, fixed_count);
        return 0;
    }

    /* Daemon mode: run periodically */
    for (int pass = 0; pass < 36; pass++) {
        sleep(pass == 0 ? 10 : 60);
        checked_count = fixed_count = 0;
        scan_dir(root, 0);
        if (fixed_count > 0)
            fprintf(stderr, "nezha-atx-repair: pass %d checked=%d fixed=%d\n",
                    pass, checked_count, fixed_count);
    }
    return 0;
}
