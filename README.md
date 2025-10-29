# Packaging Line Discrete-Event Simulation (DES)

## Overview

During my studies, I worked part-time in a warehouse and gained firsthand experience with how production lines operate — from case forming to palletizing. Observing the flow of goods, the bottlenecks between machines, and the coordination required across different stages inspired this project.

This simulation models a **complete beverage packaging line**, where cases are formed, separated, bottled with different flavors, glued or dated, and finally palletized for shipment. The system captures real-world behaviors such as buffer limits, shift schedules, and downtime events.

The goal is to reproduce how an actual warehouse line functions under varying conditions and to analyze how production efficiency can be optimized through discrete-event simulation.

The simulated layout follows:

CaseFormer → Separator → B1 → B2 → B3 → B4 → Glue/Date → Palletizer

Intermediate **buffers** store cases between stages to prevent bottlenecks. The model uses **discrete-event simulation** (DES) via a priority queue (`heapq`) to manage events in simulated time.

---

## Features

- Full **discrete-event simulation** of an industrial packaging line.
- Configurable **machine cycle times**, **buffer sizes**, and **shift schedules**.
- Models **breaks, downtimes, and pause-resume behavior**.
- Outputs detailed CSV logs for analysis:
  - `pallet_events.csv` – timestamps of pallet completions.
  - `sim_summary.csv` – key configuration and output metrics.
  - `line_log.csv` – operational events with pallet progress tracking.
- Deterministic reproducibility with random seed control.

---

## System Workflow

| Stage            | Function                 | Description                                          |
| ---------------- | ------------------------ | ---------------------------------------------------- |
| CaseFormer (CF)  | `try_CF` / `done_CF`     | Forms cases at a fixed rate (`t_caseformer`).        |
| Separator (SEP)  | `try_SEP` / `done_SEP`   | Separates cases into lanes for bottlers.             |
| Bottlers (B1–B4) | `try_B(k)` / `done_B(k)` | Four stations process different drink flavors.       |
| Glue/Date        | `try_GLUE` / `done_GLUE` | Applies glue or date labels on cases.                |
| Palletizer       | `try_PAL` / `done_PAL`   | Stacks cases onto pallets; tracks pallet completion. |

Each stage uses buffers to synchronize flow. Event-driven triggers ensure that retries occur only when upstream/downstream conditions allow, eliminating blind polling.

---

## Configuration Parameters

Located in the `CONFIG` dictionary at the top of `main.py`.

| Parameter                     | Type  | Description                                                       |
| ----------------------------- | ----- | ----------------------------------------------------------------- |
| `start_hm`, `end_hm`          | tuple | Shift start and end times.                                        |
| `breaks`                      | list  | List of scheduled breaks (HH,MM,HH,MM).                           |
| `downtimes`                   | list  | Optional unscheduled stoppages.                                   |
| `cases_per_pallet`            | int   | Number of cases per pallet.                                       |
| `t_caseformer`, `t_separator` | float | Time per case (seconds).                                          |
| `bottler_range`               | tuple | Uniform min/max for bottler process times.                        |
| `t_glue`                      | float | Glue/Date station cycle time.                                     |
| `palletizer_dist`             | str   | Distribution type for palletizer cycle time (`uniform` or `tri`). |
| `palletizer_params`           | tuple | Parameters for palletizer time distribution.                      |
| `buffers`                     | list  | Buffer capacities between stations.                               |
| `seed`                        | int   | Random seed for reproducibility.                                  |

---

## Execution

### Requirements

- Python 3.8+
- Standard library only (`heapq`, `csv`, `random`, `math`, `dataclasses`)

### Run

```bash
python3 main.py
```

This executes a full shift simulation and writes the output CSV files to the working directory.

---

## Output Files

| File                    | Description                                             |
| ----------------------- | ------------------------------------------------------- |
| **`pallet_events.csv`** | Sequence number, event time (seconds & clock time).     |
| **`sim_summary.csv`**   | Summary metrics including cases and pallets produced.   |
| **`line_log.csv`**      | Detailed operation log with events and progress status. |

---

## Data Dictionary (Outputs)

### `pallet_events.csv`

| Column                   | Description                          |
| ------------------------ | ------------------------------------ |
| `pallet_seq`             | Pallet index in order of completion. |
| `time_sec_from_midnight` | Event time in seconds since 00:00.   |
| `clock_time`             | Readable clock time.                 |

### `sim_summary.csv`

| Column            | Description                             |
| ----------------- | --------------------------------------- |
| `cases_out`       | Total number of cases produced.         |
| `pallets_out`     | Total pallets completed.                |
| `bottler_range_s` | Bottler cycle time range.               |
| `glue_time_s`     | Time per glue operation.                |
| `palletizer`      | Palletizer distribution and parameters. |
| `buffers`         | List of buffer capacities.              |

### `line_log.csv`

| Column            | Description                                    |
| ----------------- | ---------------------------------------------- |
| `event`           | Label of event (e.g., BREAK_START, BREAK_END). |
| `time_sec`        | Simulation time in seconds.                    |
| `clock`           | Human-readable timestamp.                      |
| `in_pallet_cases` | Current cases on incomplete pallet.            |
| `pallet_size`     | Total cases per pallet.                        |

---

## Example Use Case

The model can be used to:

- Optimize **machine-to-buffer ratios** to minimize idle time.
- Analyze **bottlenecks** in production flow.
- Estimate **output under varying shift schedules**.
- Simulate **maintenance or break schedules** to evaluate downtime effects.

---

## Author and License

Created for research and simulation of **manufacturing/warehouse packaging lines**.  
Author: Saurav Acharya
