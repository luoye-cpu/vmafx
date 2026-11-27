- **Tests:** the scratch-program source scan opens every tracked Python file
  without following a link, including a link in a parent path component, and
  with `O_NONBLOCK` on POSIX, so a tracked symlink to a FIFO is refused before
  the read can block (issue 3683).
