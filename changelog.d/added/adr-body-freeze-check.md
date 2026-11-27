- **Accepted ADR body freeze.** CI and the pre-push hook reject changes to
  frozen decisions. They allow dated updates, reference edits, supersession
  notes, link repairs and prose reflow. The pre-push hook checks every pushed
  ref from Git's push protocol. For the `ADR-Body-Edit` waiver and its
  required reason, see `docs/adr/_index_fragments/_header.md` under
  **Conventions**.
