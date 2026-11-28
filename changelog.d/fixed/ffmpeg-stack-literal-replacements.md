- FFmpeg patch-stack refresh used to fail with `bad escape \U` when a Windows
  remote path such as `C:\Users\runner\ffmpeg.git` was copied into a Docker
  ARG. Refresh now writes replacement values literally in Docker ARGs, pinned
  build configuration, and the README replay claim.
