- **CI:** the ADR body-freeze check now classifies Markdown with
  `markdown-it-py` CommonMark line maps instead of its hand-written line
  classifier; an HTML comment can no longer hide a fence, a reference-like
  second paragraph line is no longer accepted as a definition, and dedenting
  a line out of an indented code block is content (issue 3684).
