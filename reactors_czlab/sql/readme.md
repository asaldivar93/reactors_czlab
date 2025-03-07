experiment table:
  id name[STR] date[DATE] volume[Float]

visiferm table:
  id experiment_id date[DATE] chn1[FLOAT] chn1_units[STR] chn2[FLOAT] chn2_units[STR]

arcph table:
  id experiment_id date[DATE] chn1[FLOAT] chn1_units[STR] chn2[FLOAT] chn2_units[STR]

analog table:
  id experiment_id date[DATE] calibration[STR] chn1[FLOAT]

actuator table:
  id experiment_id date[DATE] calibration[STR] chn1[FLOAT]

# This sensor has more channels I need to review how many
incyte table:
  id experiment_id date[DATE] chn1[FLOAT] chn2[FLOAT] ...
