- experiment table: id, name[STR], date[DATE], volume[Float]

- visiferm table:
  id, experiment_id, date[DATE], reactor[STR], value[FLOAT], units[STR]

- arcph table:
  id, experiment_id, date[DATE], reactor[STR], value[FLOAT], units[STR]

- analog table:
  id, experiment_id, date[DATE], reactor[STR], calibration[STR], value[FLOAT]

- actuator table:
  id, experiment_id, date[DATE], reactor[STR], calibration[STR], value[FLOAT]

@Bhavana because of how the OPC subscription works, we are going
to get one channel at the time, which means we'll have to commit one
channel at the time to the sql database even for tables that have
two channels

I think we'll need to modify the visiferm and arcph tables to reflect
that. We'll also need a new field reactor that I overlooked before.
I've updated the README in sql with the changes needed.

Most of the info of the sql commit is stored in the data variable
of type core.utils.PhysicalInfo with a single Channel.
data.model is the name of the table. Then you'll need
data.channels[0].value and data.channels[0].units

For the actuator and analog tables, the calibration[STR] field in the
sql tables will be under data.calibration.file

The call to the sql database would look like this:
(please modify however you see fit)

```python
our_sql_api.commit(
   reactor_id: str,
   experiment_name: str,
   timestamp: DateTime,
   data: PhysicalInfo,
)
```
