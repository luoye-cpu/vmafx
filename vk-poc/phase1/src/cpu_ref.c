/*
 * cpu_ref.c — CPU reference tool for the VMAFx VK PoC Phase 1 integer ADM gate.
 *
 * Links the UNMODIFIED static library built from VMAFx master
 * af5ec5c9435b396ae13f0fffc9b29d040786cea8 and
 * drives the public API only. Reads two yuv420p8 raw fixtures, registers the
 * "adm" feature with debug=true, and writes the Phase 1 contract file
 * (see README.md, "Output contract"):
 *
 *   # tool=cpu-ref libvmaf=<vmaf_version()> fixture=<name> frames=<n> w=<w> h=<h>
 *   # fields: frame s0num s0den s1num s1den s2num s2den s3num s3den num den adm2
 *   frame=<i> s0num=%a s0den=%a ... adm2=%a | s0num=%.9g ... adm2=%.9g
 *
 * Every payload value is printf("%a", (double)value): the value returned by
 * vmaf_feature_score_at_index(), printed as a hex float. Per the CPU source
 * (core/src/feature/integer_adm.c integer_compute_adm), the per-scale
 * sNnum/sNden scores are float values (double scores[8] hold exactly-promoted
 * floats), while num/den are DOUBLE accumulators (num += num_scale with num,
 * den declared double) after the numden_limit clamp, and adm2 = num/den is
 * double division. Printing the returned double verbatim keeps every bit; no
 * float narrowing is applied. The decimal mirror after '|' is for humans and
 * is ignored by tools/compare.py.
 *
 * Any non-zero API return, short read, or failed feature lookup is fatal —
 * no silent substitution of values.
 *
 * Build (see build.sh / build.cmd):
 *   gcc -O2 -I <libvmaf-include> src/cpu_ref.c -o build/cpu_ref.exe \
 *       <libvmaf-static-lib> -lstdc++ -lm
 *
 * Run:
 *   cpu_ref.exe --ref <ref.yuv> --dis <dis.yuv> --w W --h H --frames N \
 *               --fixture <name> --out <out.txt>
 */

#include <errno.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libvmaf/libvmaf.h>
#include <libvmaf/feature.h>
#include <libvmaf/picture.h>

#define N_VALUE_FIELDS 11

/*
 * Feature names in output-contract column order (s0num..s3den = per-scale
 * num/den from debug=true; num/den = per-frame totals; adm2 = final score).
 * "VMAF_integer_feature_adm2_score" is the internal feature name; the CLI JSON
 * prints it as "integer_adm2".
 */
static const char *feature_names[N_VALUE_FIELDS] = {
    "integer_adm_num_scale0",
    "integer_adm_den_scale0",
    "integer_adm_num_scale1",
    "integer_adm_den_scale1",
    "integer_adm_num_scale2",
    "integer_adm_den_scale2",
    "integer_adm_num_scale3",
    "integer_adm_den_scale3",
    "integer_adm_num",
    "integer_adm_den",
    "VMAF_integer_feature_adm2_score",
};

static const char *column_names[N_VALUE_FIELDS] = {
    "s0num", "s0den", "s1num", "s1den", "s2num", "s2den",
    "s3num", "s3den", "num", "den", "adm2",
};

static void die(int code, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("cpu_ref: FATAL: ", stderr);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(code);
}

static void *xmalloc(size_t n)
{
    void *p = malloc(n);
    if (!p)
        die(3, "out of memory (%zu bytes)", n);
    return p;
}

static void usage(void)
{
    fprintf(stderr,
        "usage: cpu_ref.exe --ref <ref.yuv> --dis <dis.yuv> --w W --h H\n"
        "                   --frames N --fixture <name> --out <out.txt>\n");
    exit(2);
}

static unsigned parse_uint(const char *s)
{
    char *end = NULL;
    unsigned long v = strtoul(s, &end, 10);
    if (!end || *end != '\0' || end == s || v > 0xFFFFFFFFul)
        usage();
    return (unsigned)v;
}

/* Copy a full yuv420p8 frame (all three planes) into an allocated VmafPicture.
 * Row-by-row memcpy honours the allocator's padded strides. integer_adm only
 * consumes luma, but a fully populated picture is the safest contract. */
static void copy_yuv420p8(VmafPicture *pic, const uint8_t *src,
                          unsigned w, unsigned h)
{
    const unsigned cw = w / 2, ch = h / 2;
    const size_t plane_bytes[3] = {
        (size_t)w * h, (size_t)cw * ch, (size_t)cw * ch
    };
    const uint8_t *psrc = src;

    for (int p = 0; p < 3; p++) {
        const unsigned pw = (p == 0) ? w : cw;
        const unsigned ph = (p == 0) ? h : ch;
        uint8_t *dst = (uint8_t *)pic->data[p];
        for (unsigned y = 0; y < ph; y++)
            memcpy(dst + (size_t)y * pic->stride[p], psrc + (size_t)y * pw, pw);
        psrc += plane_bytes[p];
    }
}

int main(int argc, char **argv)
{
    const char *ref_path = NULL, *dis_path = NULL;
    const char *fixture_name = NULL, *out_path = NULL;
    unsigned w = 0, h = 0, frames = 0, cpumask = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--ref") && i + 1 < argc) ref_path = argv[++i];
        else if (!strcmp(argv[i], "--dis") && i + 1 < argc) dis_path = argv[++i];
        else if (!strcmp(argv[i], "--w") && i + 1 < argc) w = parse_uint(argv[++i]);
        else if (!strcmp(argv[i], "--h") && i + 1 < argc) h = parse_uint(argv[++i]);
        else if (!strcmp(argv[i], "--frames") && i + 1 < argc) frames = parse_uint(argv[++i]);
        else if (!strcmp(argv[i], "--fixture") && i + 1 < argc) fixture_name = argv[++i];
        else if (!strcmp(argv[i], "--cpumask") && i + 1 < argc) {
            /* VMAFx semantics: the mask lists instructions to DISABLE
             * (libvmaf.h:212-217 — 8 = no AVX2, 16 = no AVX512). Used to
             * show the reference is ISA-invariant. */
            cpumask = parse_uint(argv[++i]);
        } else if (!strcmp(argv[i], "--out") && i + 1 < argc) out_path = argv[++i];
        else usage();
    }
    if (!ref_path || !dis_path || !fixture_name || !out_path ||
        !w || !h || !frames)
        usage();
    if (w % 2 || h % 2)
        die(2, "yuv420p8 requires even dimensions (w=%u h=%u)", w, h);

    const size_t frame_bytes = (size_t)w * h + 2 * ((size_t)(w / 2) * (h / 2));
    uint8_t *ref_buf = xmalloc(frame_bytes);
    uint8_t *dis_buf = xmalloc(frame_bytes);

    FILE *fref = fopen(ref_path, "rb");
    if (!fref)
        die(3, "cannot open ref fixture %s: %s", ref_path, strerror(errno));
    FILE *fdis = fopen(dis_path, "rb");
    if (!fdis)
        die(3, "cannot open dis fixture %s: %s", dis_path, strerror(errno));

    /* n_threads=1 for determinism; log level ERROR so libvmaf prints the reason
     * for any failed lookup to stderr. cpumask defaults to 0 (no instruction
     * set disabled); --cpumask lets the caller force the scalar/SIMD variant. */
    VmafConfiguration cfg = {
        .log_level = VMAF_LOG_LEVEL_ERROR,
        .n_threads = 1,
        .n_subsample = 0,
        .cpumask = cpumask,
        .gpumask = 0,
    };
    VmafContext *vmaf = NULL;
    int ret = vmaf_init(&vmaf, cfg);
    if (ret)
        die(3, "vmaf_init failed: ret=%d (%s)", ret, strerror(-ret));

    VmafFeatureDictionary *dict = NULL;
    ret = vmaf_feature_dictionary_set(&dict, "debug", "true");
    if (ret)
        die(3, "vmaf_feature_dictionary_set(debug=true) failed: ret=%d (%s)",
            ret, strerror(-ret));
    ret = vmaf_use_feature(vmaf, "adm", dict);
    if (ret) {
        /* On failure the context does NOT take ownership; free the dict. */
        vmaf_feature_dictionary_free(&dict);
        die(3, "vmaf_use_feature(adm, debug=true) failed: ret=%d (%s) "
               "(see libvmaf stderr log above)", ret, strerror(-ret));
    }

    FILE *out = fopen(out_path, "w");
    if (!out)
        die(3, "cannot open output %s: %s", out_path, strerror(errno));

    fprintf(out, "# tool=cpu-ref libvmaf=%s fixture=%s frames=%u w=%u h=%u\n",
            vmaf_version(), fixture_name, frames, w, h);
    fprintf(out, "# fields: frame s0num s0den s1num s1den s2num s2den "
                 "s3num s3den num den adm2\n");

    for (unsigned i = 0; i < frames; i++) {
        if (fread(ref_buf, 1, frame_bytes, fref) != frame_bytes)
            die(3, "short read on %s at frame %u (expected %zu bytes)",
                ref_path, i, frame_bytes);
        if (fread(dis_buf, 1, frame_bytes, fdis) != frame_bytes)
            die(3, "short read on %s at frame %u (expected %zu bytes)",
                dis_path, i, frame_bytes);

        VmafPicture ref_pic, dis_pic;
        ret = vmaf_picture_alloc(&ref_pic, VMAF_PIX_FMT_YUV420P, 8, w, h);
        if (ret)
            die(3, "vmaf_picture_alloc(ref) failed at frame %u: ret=%d (%s)",
                i, ret, strerror(-ret));
        ret = vmaf_picture_alloc(&dis_pic, VMAF_PIX_FMT_YUV420P, 8, w, h);
        if (ret)
            die(3, "vmaf_picture_alloc(dis) failed at frame %u: ret=%d (%s)",
                i, ret, strerror(-ret));
        copy_yuv420p8(&ref_pic, ref_buf, w, h);
        copy_yuv420p8(&dis_pic, dis_buf, w, h);

        ret = vmaf_read_pictures(vmaf, &ref_pic, &dis_pic, i);
        if (ret)
            die(3, "vmaf_read_pictures failed at index %u: ret=%d (%s)",
                i, ret, strerror(-ret));
        /* On success both pictures' references belong to the context. */
    }

    /* Flush: signal end of sequence, then collect the scores. */
    ret = vmaf_read_pictures(vmaf, NULL, NULL, 0);
    if (ret)
        die(3, "vmaf_read_pictures(flush) failed: ret=%d (%s)",
            ret, strerror(-ret));

    for (unsigned i = 0; i < frames; i++) {
        double vals[N_VALUE_FIELDS];
        for (int k = 0; k < N_VALUE_FIELDS; k++) {
            double score = 0.0;
            ret = vmaf_feature_score_at_index(vmaf, feature_names[k],
                                              &score, i);
            if (ret)
                die(3, "vmaf_feature_score_at_index(\"%s\", index=%u) failed: "
                       "ret=%d (%s)", feature_names[k], i, ret, strerror(-ret));
            vals[k] = score; /* print the stored double verbatim (see above) */
        }

        fprintf(out, "frame=%u", i);
        for (int k = 0; k < N_VALUE_FIELDS; k++)
            fprintf(out, " %s=%a", column_names[k], (double)vals[k]);
        fprintf(out, " |");
        for (int k = 0; k < N_VALUE_FIELDS; k++)
            fprintf(out, " %s=%.9g", column_names[k], (double)vals[k]);
        fprintf(out, "\n");
    }

    if (fclose(out))
        die(3, "fclose(%s) failed: %s", out_path, strerror(errno));
    fclose(fref);
    fclose(fdis);
    vmaf_close(vmaf);
    free(ref_buf);
    free(dis_buf);

    printf("cpu_ref: wrote %s (%u frames, %ux%u, libvmaf %s)\n",
           out_path, frames, w, h, vmaf_version());
    return 0;
}
