- Share the encode-subprocess timeout: `pkg/ffencode` now exports
  `EncodeTimeout()`, and `pkg/encoder` and `pkg/encodeprofile` call it instead
  of keeping three copies of the 60-minute default and the
  `VMAFX_TUNE_ENCODE_TIMEOUT` parser (#3075).
