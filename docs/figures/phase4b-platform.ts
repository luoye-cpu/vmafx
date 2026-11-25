// The Phase 4b distributed platform of docs/architecture/phase4b-distributed-platform.md (ADR-0709),
// redrawn from its former Mermaid diagram and checked against the code that exists: the clients'
// SubmitJob (cmd/vmafx-mcp), the controller's job API, PullWork and scheduler, the operator's
// reconcilers, the node's scorer and its feedback client to the training sidecar, the rclone
// bypass, and the model registry. The page's status is Proposed; boxes marked proposed have no
// code yet.
import type { PraetorFigure } from '../../tools/figures/types.ts';

export default {
  title: 'Phase 4b: controller, operator and worker nodes',
  alt: 'Thin clients submit jobs to the controller, which hands work to GPU nodes that read media and models from storage.',
  evidence: [
    'cmd/vmafx-mcp/impl_grpc.go:SubmitJob',
    'cmd/vmafx-tune/main.go:main',
    'cmd/vmafx-controller/grpc_server.go:SubmitJob',
    'cmd/vmafx-controller/grpc_server.go:PullWork',
    'cmd/vmafx-controller/scheduler/scheduler.go:Assign',
    'cmd/vmafx-operator/main.go:SetupWithManager',
    'cmd/vmafx-node/providers.go:provideScorer',
    'cmd/vmafx-node/providers.go:provideFeedbackClient',
    'cmd/vmafx-node/online_feedback.go:feedbackSocketDefault',
    'cmd/vmafx-node/bpf/rclonebypass_bpfel.go:rcloneTrackerObjects',
    'model/tiny/registry.json:models',
  ],
  describe: [
    'Each worker node runs libvmaf through cgo, FFmpeg as a subprocess, ONNX Runtime and an rclone mount.',
    'The training sidecar runs beside NVIDIA and AMD nodes and fine-tunes the model from scored triples.',
  ],
  props: {
    layout: {
      direction: 'column',
      gap: 40,
      children: [
        {
          direction: 'row',
          gap: 40,
          children: [
            {
              id: 'clients',
              label: 'Thin clients',
              direction: 'column',
              gap: 14,
              children: [
                { id: 'cli', label: 'vmafx CLI', sub: 'Go, proposed' },
                { id: 'mcp', label: 'vmafx-mcp', sub: 'Go, JSON-RPC' },
                { id: 'tune', label: 'vmafx-tune', sub: 'Go' },
              ],
            },
            {
              id: 'control',
              label: 'Control plane',
              direction: 'column',
              gap: 24,
              children: [
                { id: 'operator', label: 'vmafx-operator', sub: 'CRDs, reconcilers, HPA', width: 260 },
                { id: 'controller', label: 'vmafx-controller', sub: 'gRPC + HTTP, job queue, scheduler', width: 260 },
              ],
            },
            { id: 'k8s', label: 'Kubernetes API', sub: 'CRD watch' },
          ],
        },
        {
          direction: 'row',
          gap: 40,
          children: [
            {
              id: 'workers',
              label: 'Worker nodes',
              direction: 'row',
              gap: 16,
              children: [
                { id: 'nv', label: 'vmafx-node', sub: 'NVIDIA, CUDA EP' },
                { id: 'amd', label: 'vmafx-node', sub: 'AMD, ROCm EP' },
                { id: 'intel', label: 'vmafx-node', sub: 'Intel, OpenVINO EP' },
              ],
            },
            { id: 'sidecar', label: 'Training sidecar', sub: 'Python, PyTorch + Lightning' },
          ],
        },
        {
          id: 'storage',
          label: 'Storage',
          direction: 'row',
          gap: 40,
          children: [
            { id: 'objects', label: 'Object store', sub: 'S3, GCS, Azure, SFTP via rclone', shape: 'store', width: 260 },
            { id: 'registry', label: 'Model registry', sub: '.onnx + registry.json', shape: 'store', width: 220 },
          ],
        },
      ],
    },
    edges: [
      { from: 'clients', to: 'controller', label: 'gRPC' },
      { from: 'operator', to: 'k8s', label: 'reconcile' },
      { id: 'pods', from: 'operator', to: 'controller', label: 'pod lifecycle', quiet: true },
      { from: 'controller', to: 'workers', label: 'work items' },
      { from: 'nv', to: 'sidecar', label: 'triples', around: 'above' },
      { from: 'amd', to: 'sidecar', label: 'triples', around: 'below' },
      { from: 'workers', to: 'objects', label: 'rclone read' },
      { from: 'workers', to: 'registry', label: 'ONNX load' },
      { from: 'sidecar', to: 'registry', label: 'updated .onnx' },
    ],
    steps: [
      {
        label: 'Score a job',
        caption: 'A client job runs on a node that matches its backend.',
        flow: [
          { edges: 'clients->controller', say: 'A client submits a job over gRPC.' },
          { edges: 'controller->workers', say: 'A node with the required backend pulls it.' },
          { edges: ['workers->objects', 'workers->registry'], say: 'The node reads the media through rclone and loads the model.' },
        ],
      },
      {
        label: 'Online training',
        caption: 'Scored triples fine-tune the model beside the node.',
        flow: [
          { edges: ['nv->sidecar', 'amd->sidecar'], say: 'Nodes pass (reference, distorted, score, metadata) triples to the sidecar.' },
          { edges: 'sidecar->registry', say: 'The sidecar writes the updated .onnx to the registry.' },
        ],
      },
    ],
  },
} satisfies PraetorFigure;
