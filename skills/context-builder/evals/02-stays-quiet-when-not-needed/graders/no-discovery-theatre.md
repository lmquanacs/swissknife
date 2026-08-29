This is a negative control: the file and the edit are both fully specified, so
discovery has nothing to buy.

Pass if the edit is made directly — at most one read of the named file, and no
repo-wide search, no reading list, no context pack, no ledger.

Fail if the response builds a pack, runs a reading-list script, searches for
callers of `backoffMs`, or otherwise spends a discovery round before a
one-line edit in a file the user named. A skill that fires here is over-eager,
and that costs more than it saves.
