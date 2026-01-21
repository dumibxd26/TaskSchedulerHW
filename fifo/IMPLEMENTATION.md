# Descrierea Implementării - Sisteme de Schedulare Distribuite

## Arhitectura Sistemului

Implementarea sistemelor de schedulare pentru task-uri în sisteme distribuite folosește o arhitectură master-slave uniformă pentru toți cei trei algoritmi (FIFO, Round-Robin, Priority Queue). Sistemul constă din trei componente principale:

### 1. Scheduler (Master)

Scheduler-ul este un serviciu FastAPI care gestionează distribuția job-urilor către workers. Toate implementările partajează următoarele caracteristici comune:

- **Worker Registry**: Un dicționar thread-safe care menține informații despre workers înregistrați (worker_id, număr de core-uri, last_seen timestamp)
- **Heartbeat Mechanism**: Workers trimit heartbeat-uri periodice pentru a indica că sunt încă activi
- **Long-polling**: Endpoint-ul `/next` folosește long-polling (timeout de 10000ms pentru Round-Robin și Priority, 1000ms pentru FIFO) pentru a reduce overhead-ul de polling
- **Threading**: Folosește `threading.Lock` și `threading.Condition` pentru sincronizare între thread-uri
- **Arrival Thread**: Un thread dedicat care verifică periodic job-urile din `pending_jobs` și le mută în coada de procesare când momentul lor de sosire (`arrival_time_ms`) este atins

**Diferențe între algoritmi**:

- **FIFO**: Folosește o coadă `deque` (`ready_q`) pentru a păstra job-urile în ordinea strictă de sosire. Job-urile sunt procesate non-preemptiv (execuție completă până la finalizare).
- **Round-Robin**: Folosește o coadă `deque` (`ready_q`) similară cu FIFO, dar implementează preemptare prin "quantum slicing". Fiecare job primește un quantum de timp (`quantum_ms`), iar dacă nu se termină, este reintrodus în coadă cu `remaining_ms` actualizat.
- **Priority Queue**: Folosește un heap (priority queue) implementat cu `heapq` (`ready_heap`). Job-urile sunt sortate după prioritate (valoare numerică, mai mică = prioritate mai mare), apoi după arrival time și sequence number pentru tie-breaking. Execuția este non-preemptivă.

### 2. Worker (Slave)

Worker-ul este un serviciu FastAPI care execută job-urile primite de la scheduler. Arhitectura worker-ului este identică pentru toți algoritmii:

- **Thread per Core**: Fiecare core are propriul thread dedicat (`core_thread`) care execută job-uri
- **Async Main Loop**: Un event loop asyncio care gestionează comunicarea cu scheduler-ul (long-polling pentru `/next`, raportare la `/done`)
- **Semaphore-based Assignment**: Folosește semafoare pentru a bloca thread-urile de core până când primește un job de la scheduler
- **Metrici**: Colectează CPU și memory usage folosind `psutil` înainte și după execuție (pentru FIFO și Priority)
- **Heartbeat Loop**: Trimite periodic heartbeat-uri către scheduler pentru a indica că worker-ul este activ

**Execuție**:
- **FIFO și Priority**: Execuție non-preemptivă - job-ul rulează complet (`time.sleep(execution_ms / 1000.0 / SPEEDUP)`)
- **Round-Robin**: Execuție preemptivă - job-ul rulează doar pentru `quantum_ms`, apoi este întrerupt și raportează `remaining_ms` înapoi la scheduler

### 3. Client și Automatizare

- **`submit_runs.py`**: Client care pornește un run și monitorizează finalizarea prin polling la endpoint-ul `/status`
- **`run_matrix.py`** (FIFO și Priority): Script pentru rularea automată a experimentelor cu toate combinațiile de replicas × cores, generând rezultate structurate și un `summary.csv`
- **`generate_dataset.py`**: Script pentru generarea dataset-urilor CSV cu job-uri care se suprapun (overlapping) sau cu sosiri în burst

## Structuri de Date și Algoritmi de Schedulare

### FIFO (First-In-First-Out)

```python
ready_q: Deque[str]  # Coadă FIFO - popleft() pentru extragere
pending_jobs: Dict[str, JobState]  # Job-uri care nu au ajuns încă
running_jobs: Dict[str, int]  # job_id -> finish_ms (pentru tracking sloturi)
```

Algoritmul procesează job-urile strict în ordinea de sosire. Când un worker cere un job, scheduler-ul calculează cel mai devreme moment când un slot devine disponibil și alocă job-ul.

### Round-Robin

```python
ready_q: Deque[str]  # Coadă circulară
pending_ids: List[str]  # Job-uri sortate după arrival_time
JobState.remaining_ms: int  # Timp rămas pentru job
```

Algoritmul alocă fiecărui job un quantum de timp (`quantum_ms`). Dacă job-ul nu se termină în quantum, este preemptat și reintrodus în coadă. Acest mecanism asigură fairness între job-uri și previne "starvation".

### Priority Queue

```python
ready_heap: List[Tuple[int, int, int, str]]  # (priority, arrival, seq, job_id)
pending_heap: List[Tuple[int, int, str]]  # (arrival, seq, job_id)
```

Algoritmul folosește un min-heap sortat după prioritate (mai mică = mai mare prioritate), apoi după arrival time și sequence number. Job-urile cu prioritate mai mare sunt procesate primul, indiferent de ordinea de sosire.

## Simularea Timpului și Speedup

Toate implementările folosesc un factor de speedup (default: 20000x) pentru a accelera simularea. Timpul real (wall time) este convertit în timp simulat folosind formula:

```python
simulated_time = (wall_time - start_wall_time) * speedup
```

Această abordare permite rularea experimentelor cu 1000+ job-uri în câteva secunde, în loc de ore. Scheduler-ul menține un `current_sim_ms` care urmărește timpul simulat actual, avansând când job-urile sunt procesate sau finalizate.

## Colectarea Metricilor

La finalizarea unui job, scheduler-ul calculează următoarele metrici:

- **`waiting_time_ms`**: `start_time_ms - arrival_time_ms` (timp în coadă)
- **`execution_time_ms`**: `finish_time_ms - start_time_ms` (timp efectiv execuție)
- **`response_time_ms`**: `finish_time_ms - arrival_time_ms` (timp total în sistem)
- **`slowdown`**: `response_time_ms / service_time_ms` (raport între timp total și timp de serviciu)
- **`cpu_usage_percent`**: CPU usage mediu (colectat cu `psutil`)
- **`memory_usage_mb`**: Memory usage maxim (colectat cu `psutil`)

Pentru Round-Robin, se colectează și:
- **`slices`**: Număr de quantum-uri în care a fost procesat job-ul
- **`preemptions`**: Număr de preemptări (slices - 1)

## Structura Rezultatelor

Rezultatele sunt salvate în CSV-uri cu următoarea structură:

**FIFO și Priority**:
```
results/
├── replicas_2_cores_2/
│   ├── results_jobs_dataset_name.csv
│   └── results_run_dataset_name.csv
├── replicas_2_cores_4/
│   └── ...
└── summary.csv  # Statistici pentru toate configurațiile
```

**Round-Robin**:
```
results/
├── results_jobs_<run_id>.csv
└── results_run_<run_id>.csv
```

Fiecare `results_jobs_*.csv` conține metrici per job, iar `results_run_*.csv` conține statistici agregate (mean, p50, p95, p99 pentru response time, waiting time, execution time).

## Testare și Validare

Implementările au fost testate cu:
- **Dataset-uri cu overlapping**: Job-uri care se suprapun pentru a forma cozi
- **Burst arrivals**: Multiple job-uri care ajung simultan (pentru FIFO)
- **Configurații variate**: 2-16 replici × 2-16 core-uri (16 combinații pentru FIFO și Priority)
- **Quantum sizes variate**: Pentru Round-Robin, teste cu diferite valori de `quantum_ms`

Rezultatele confirmă că fiecare algoritm respectă corect principiile sale fundamentale: FIFO procesează job-urile în ordinea strictă de sosire, Round-Robin asigură fairness prin quantum slicing, iar Priority Queue procesează job-urile în funcție de prioritate.
