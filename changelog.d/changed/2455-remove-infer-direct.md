- Remove the unimplemented `pkg/ai.Registry.InferDirect` stub, its
  `ErrDirectInferNotImplemented` sentinel and its unit test. Q-132 settled on
  the `vmafx-ort-runner` subprocess as the single ONNX integration, so
  inference is exposed only through `Registry.Infer` (#2455).
