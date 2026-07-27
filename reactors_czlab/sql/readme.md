# Database schema

Create it with:

```bash
psql -f Bioreactor.sql
```

## `data`

One row per channel reading. The OPC subscription delivers one channel at a
time, so a two channel sensor produces two rows with the same timestamp
rather than one row with two values.

| Column | Type | Source |
| --- | --- | --- |
| `id` | SERIAL | primary key |
| `node_id` | TEXT | OPC node id, e.g. `ns=2;i=9` |
| `date` | TIMESTAMP(3) | when the client received the change |
| `reactor` | TEXT | `R0`, `R1`, ... |
| `name` | TEXT | device name, e.g. `ph`, `do`, `biomass`, `pwm0` |
| `channel` | TEXT | channel units, e.g. `pH`, `oC`, `ppm`, `445` |
| `value` | FLOAT | the reading |

`reactor`, `name` and `channel` are the three parts of the OPC browse name
`<reactor>:<name>:<channel>`, split by `OpcClient.match_tree`.

Readings equal to `core.data.ERROR_VALUE` are dropped by the client and
never reach this table.

## `experiments`

Not written by any code yet. It is here for the planned "group a run under a
named experiment" feature; until something populates it, `data` stands alone.
