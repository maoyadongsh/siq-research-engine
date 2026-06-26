# Hermes Profiles Boundary

Hermes runtime state defaults to:

```text
data/hermes/home
```

The API and top-level startup scripts read that location through
`SIQ_HERMES_HOME` and `SIQ_HERMES_PROFILES_ROOT`. The lightweight files in this
directory document the profile boundary without keeping Hermes runtime state in
source-owned paths.

Set these variables to override the default:

```text
SIQ_HERMES_HOME=/path/to/hermes_home
SIQ_HERMES_PROFILES_ROOT=/path/to/hermes_home/profiles
```
