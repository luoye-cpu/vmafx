- **Tests:** the vmaf-mcp suite now skips instead of raising `InvalidVmafBinary`
  when no CLI under test is built. `conftest.py` resolves the binary before its
  autouse fixture substitutes the missing `build/tools/vmaf` path, and the
  ADR-1117 end-to-end guard consumes that resolved value (issue 3644).
