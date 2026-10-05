# Supplier migration

A shared view of the active supplier slice. These blocks read Fleet's accepted records.

## Accepted work

::work{id=WORK block=supplier-work}

## Question desk

::attention{id=ATTENTION block=supplier-question}

## Recent runs

::runs{project=PROJECT since=7d block=supplier-runs}

## Missing records stay visible

::work{id=missing-work block=missing-record}

The plan stays readable even when a referenced record is unavailable.
