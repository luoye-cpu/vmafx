- **Workflow size checks now measure the LF bytes GitHub reads.** On a
  Windows checkout with `core.autocrlf=true`, CRLF line endings made every
  workflow line count one extra byte, so files just under the 64 KiB bound
  failed locally even though their committed blob passed. The checker now
  normalizes CRLF to LF before measuring, and `.gitattributes` pins workflow
  files to LF so the checkout and the blob agree. A regression test covers
  a CRLF fixture whose LF blob is under the bound and an over-bound fixture
  that still reports its LF size.
