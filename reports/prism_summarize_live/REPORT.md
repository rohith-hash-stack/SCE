# prism summarize: live run

Code `571a5f9`, model `qwen2.5-coder:14b-instruct-q8_0`, Kaggle T4 x2.

| repo | functions | summarized | coverage | weight | lint any | gate | left | kill test | resume kept all | minutes |
|---|---|---|---|---|---|---|---|---|---|---|
| click | 512 | 512 | 1.0 | 0.5 | 0.004 | pass | 0 | killed, 120 on disk, valid True | True | 6.8 |

## click

```
{
 "lint": {
  "axis_label": 0.0,
  "filler": 0.0,
  "identifier": 0.004,
  "callee_list": 0.0,
  "any": 0.004,
  "n": 512
 },
 "asks": [
  {
   "question": "how are command line options parsed from argv",
   "route": "natural_language",
   "summary_weight": 0.5,
   "top3": [
    "click.parser.OptionParser.parse_args",
    "click.core.BaseCommand.main",
    "click._compat._get_argv_encoding"
   ]
  },
  {
   "question": "where is the help text formatted",
   "route": "natural_language",
   "summary_weight": 0.5,
   "top3": [
    "click.formatting.HelpFormatter.write_text",
    "click.core.Command.format_help_text",
    "click.formatting.join_options"
   ]
  },
  {
   "question": "click.echo",
   "route": "identifier",
   "summary_weight": 0.5,
   "top3": [
    "click.testing.CliRunner._getchar",
    "click.testing._pause_echo",
    "click.testing.EchoingStdin.readlines"
   ]
  }
 ]
}
```

Sample summaries:

- `click._compat._AtomicFile.__enter__`: Enters context for atomic file handling.
- `click._compat._AtomicFile.__exit__`: Exits context, closes atomic file.
- `click._compat._AtomicFile.__getattr__`: Delegates attribute access to the wrapped file.
- `click._compat._AtomicFile.__init__`: Initializes atomic file, sets up temporary and real filenames.
- `click._compat._AtomicFile.__repr__`: Returns a string representation of an atomic file object.
- `click._compat._AtomicFile.close`: Closes atomic file, replaces temp with real if needed.
- `click._compat._AtomicFile.name`: Returns name of the atomic file.
- `click._compat._FixupStream.__getattr__`: Retrieves attributes from the underlying stream.
- `click._compat._FixupStream.__init__`: Initializes a stream wrapper that adjusts stream properties.
- `click._compat._FixupStream.read1`: Reads a specified number of bytes from the stream.
- `click._compat._FixupStream.readable`: Checks if the stream is readable.
- `click._compat._FixupStream.seekable`: Checks if the stream supports seeking.
