# Evaluarea Algoritmilor de Schedulare în Sisteme Distribuite: FIFO, Round-Robin și Priority Queue

## Abstract

Acest proiect prezintă o analiză comparativă a trei algoritmi fundamentali de schedulare pentru task-uri în sisteme distribuite: FIFO (First-In-First-Out), Round-Robin și Priority Queue. Implementarea a fost realizată într-o arhitectură master-slave, unde un scheduler centralizează distribuția job-urilor către multiple workers care execută task-urile în paralel. Sistemul a fost testat cu configurații variate (2-16 replici × 2-16 core-uri) și cu dataset-uri care simulează scenarii reale, inclusiv sosiri în burst și overlapping între job-uri.

Rezultatele experimentale demonstrează că fiecare algoritm prezintă avantaje și limitări specifice. FIFO oferă simplitate și predictibilitate, dar suferă de "convoy effect" când job-urile lungi blochează job-urile scurte. Round-Robin asigură fairness prin quantum slicing, dar overhead-ul de coordonare crește semnificativ cu quantum-uri mici. Priority Queue permite procesarea rapidă a job-urilor critice, dar poate duce la starvation pentru job-urile cu prioritate mică. Analiza metricilor (response time, waiting time, slowdown, CPU/memory usage) relevă că performanța sistemului este influențată atât de politica de schedulare, cât și de overhead-ul infrastructurii (comunicare HTTP, sincronizare thread-uri).

Implementarea folosește FastAPI pentru servicii REST, threading și asyncio pentru concurență, și un factor de speedup (20000x) pentru simulare accelerată. Rezultatele sunt salvate în CSV-uri structurate, permițând analiză comparativă și reproducibilitate. Proiectul demonstrează importanța instrumentării și a metricilor pentru evaluarea obiectivă a algoritmilor de schedulare în contexte distribuite.

## Introduction

Schedularea task-urilor în sisteme distribuite reprezintă o problemă fundamentală în domeniul calculului distribuit și cloud computing. Odată cu creșterea complexității aplicațiilor moderne și a cerințelor de scalabilitate, alegerea algoritmului de schedulare potrivit devine critică pentru performanța sistemului. Algoritmii tradiționali de schedulare, dezvoltați inițial pentru sisteme monoprocesor, trebuie adaptați pentru a funcționa eficient în medii distribuite, unde overhead-ul de comunicare și sincronizare poate domina performanța.

În acest proiect, am implementat și evaluat trei algoritmi clasici de schedulare: FIFO (First-In-First-Out), Round-Robin și Priority Queue, într-un context distribuit. Scopul principal este de a înțelege cum se comportă acești algoritmi când sunt aplicați într-o arhitectură master-slave, unde un scheduler centralizează distribuția job-urilor către multiple workers care execută task-urile în paralel. Această arhitectură reflectă scenarii reale din cloud computing, unde un orchestrator (precum Kubernetes) distribuie task-uri către noduri de calcul.

Evaluarea comparativă a fost realizată prin experimente sistematice cu configurații variate de resurse (replici workers × core-uri per worker) și cu dataset-uri care simulează pattern-uri reale de sarcină, inclusiv sosiri în burst și overlapping între job-uri. Rezultatele oferă perspective practice asupra trade-off-urilor între simplitate, fairness, priorizare și overhead-ul infrastructurii, contribuind la o înțelegere mai profundă a comportamentului algoritmilor de schedulare în sisteme distribuite moderne.

## Opinion

### Opinia despre Algoritmul FIFO (First-In-First-Out) în Sisteme Distribuite

Algoritmul FIFO (First-In-First-Out) reprezintă una dintre cele mai simple și fundamentale abordări pentru schedularea task-urilor în sisteme distribuite. În contextul acestui proiect, am implementat și evaluat un sistem de schedulare FIFO într-o arhitectură master-slave, unde un scheduler centralizează distribuția job-urilor către multiple workers care execută task-urile în paralel.

#### Avantajele Algoritmului FIFO

**Simplitate și Predictibilitate**

Unul dintre principalele avantaje ale FIFO este simplitatea sa conceptuală și implementarea sa. Algoritmul respectă principiul "primul venit, primul servit" (FCFS - First Come, First Served), ceea ce înseamnă că job-urile sunt procesate strict în ordinea în care ajung în sistem. Această simplitate oferă predictibilitate ridicată: pentru orice job, putem calcula exact când va începe execuția și când se va termina, bazându-ne doar pe ordinea de sosire și pe durata de execuție a job-urilor anterioare.

În implementarea noastră, această predictibilitate se reflectă în modul în care gestionăm coada `ready_q` folosind o structură `deque` (double-ended queue), care permite inserări și extrageri eficiente la ambele capete. Job-urile sunt adăugate în coadă la momentul lor de sosire (`arrival_time_ms`) și sunt procesate strict în ordinea în care au fost adăugate.

**Echitate și Fairness**

FIFO oferă un nivel ridicat de echitate între job-uri. Toate job-urile sunt tratate la fel, indiferent de durata lor de execuție sau de complexitatea lor. Acest aspect este deosebit de important în scenarii unde nu există informații despre prioritatea sau importanța job-urilor. În sistemul nostru, fiecare job primește aceeași atenție și este procesat în ordinea strictă a sosirii sale.

**Eficiență în Scenarii cu Overhead Redus**

Pentru sisteme unde overhead-ul de context switching și preemptare este ridicat, FIFO poate fi mai eficient decât algoritmi preemptivi precum Round-Robin. În implementarea noastră, fiecare job este executat complet (non-preemptiv) până la finalizare, eliminând overhead-ul asociat cu preemptarea și reluarea execuției. Acest lucru este evidențiat în codul worker-ului, unde `time.sleep(execution_ms / 1000.0 / SPEEDUP)` simulează execuția completă a job-ului fără întreruperi.

#### Dezavantajele și Limitările FIFO

**Problema Convoy Effect**

Cel mai semnificativ dezavantaj al FIFO este fenomenul cunoscut sub numele de "convoy effect" sau "efectul convoiului". Când un job cu durată lungă de execuție ajunge primul în coadă, toate job-urile care vin după el, chiar dacă au durate foarte scurte, trebuie să aștepte până când job-ul lung se termină. Acest lucru poate duce la timpi de așteptare foarte mari pentru job-uri mici, chiar dacă ar putea fi procesate rapid.

În rezultatele noastre experimentale, observăm acest efect: pentru configurația cu 2 replici și 2 core-uri (4 sloturi totale), job-urile care ajung după un job lung au `waiting_time_ms` semnificativ mai mare. De exemplu, dacă un job cu `service_time_ms = 529` ajunge primul, job-urile care vin după el trebuie să aștepte cel puțin 529ms, chiar dacă propriul lor `service_time_ms` ar fi doar 100-200ms.

**Lipsa Priorizării**

FIFO nu oferă mecanisme de priorizare. Job-urile critice sau urgente nu pot fi procesate mai rapid decât job-urile obișnuite, dacă au ajuns mai târziu în sistem. Această limitare poate fi problematică în sisteme reale unde anumite task-uri au deadline-uri stricte sau importanță critică pentru business.

**Sensibilitate la Pattern-uri de Sarcini**

Algoritmul FIFO este foarte sensibil la pattern-urile de sosire a job-urilor. În implementarea noastră, am testat două scenarii: unul cu sosiri progresive și unul cu sosiri în "burst" (multiple job-uri la același moment). În scenariul cu burst, observăm că job-urile care ajung simultan formează cozi mari, iar FIFO procesează aceste cozi strict în ordinea în care job-urile au fost adăugate, fără a optimiza pentru durata de execuție.

#### Analiza Rezultatelor Experimentale

**Impactul Numărului de Sloturi**

Rezultatele noastre experimentale demonstrează clar impactul numărului de sloturi (replici × core-uri) asupra performanței:

- **2 replici × 2 core-uri (4 sloturi)**: Mean response time = 2230ms, Mean wait = 1853ms
- **2 replici × 16 core-uri (32 sloturi)**: Mean response time = 385ms, Mean wait = 8ms
- **16 replici × 16 core-uri (256 sloturi)**: Mean response time = 377ms, Mean wait = 0ms

Aceste rezultate arată că FIFO beneficiază semnificativ de paralelism. Cu mai multe sloturi disponibile, mai multe job-uri pot fi procesate simultan, reducând drastic timpul de așteptare. Cu 256 de sloturi, aproape toate job-urile încep execuția imediat (waiting time = 0ms), ceea ce indică faptul că sistemul are suficientă capacitate pentru a procesa job-urile fără a forma cozi semnificative.

**Comportamentul în Scenarii cu Burst**

În scenariile cu burst arrivals (multiple job-uri care ajung simultan), FIFO demonstrează comportament predictibil dar potențial problematic. Job-urile care ajung în același burst sunt procesate în ordinea în care au fost adăugate în coadă, dar toate trebuie să aștepte până când sloturile devin disponibile. Acest lucru este evidențiat în rezultatele noastre, unde job-urile cu `arrival_time_ms = 6436` (din al doilea burst) încep execuția doar după ce job-urile din primul burst (cu `arrival_time_ms = 0`) se termină sau când sloturi devin disponibile.

#### Concluzii și Recomandări

FIFO este un algoritm excelent pentru sisteme unde:
1. **Simplitatea este prioritară**: Implementarea este ușoară de înțeles și de menținut
2. **Echitatea este importantă**: Toate job-urile trebuie tratate la fel
3. **Overhead-ul de context switching este ridicat**: Execuția non-preemptivă elimină overhead-ul
4. **Paralelismul este disponibil**: Cu suficiente sloturi, FIFO poate procesa eficient multiple job-uri simultan

Cu toate acestea, FIFO nu este potrivit pentru sisteme unde:
1. **Priorizarea este necesară**: Job-urile critice nu pot fi procesate mai rapid
2. **Variabilitatea duratei de execuție este mare**: Convoy effect-ul poate duce la timpi de așteptare mari
3. **Deadline-uri stricte există**: Job-urile cu deadline-uri apropiate nu pot fi prioritizate

În contextul proiectului nostru, implementarea FIFO a demonstrat că algoritmul funcționează corect și respectă principiile sale fundamentale. Rezultatele experimentale confirmă comportamentul așteptat: cu mai multe resurse (sloturi), performanța se îmbunătățește semnificativ, iar algoritmul gestionează corect cozile formate de job-urile care ajung simultan.

---

### Opinia despre Algoritmul Priority Queue în Sisteme Distribuite

Working on the suite changed my intuition about what actually limits scheduler performance. Before the experiments I imagined the policies themselves would dominate: FIFO would be simple but unfair, priority would keep latency low for critical jobs, and round-robin would trade optimality for fairness. After instrumenting the services, it became obvious that the control plane is the first thing to saturate. Every `next` and `done` call is a REST hop through FastAPI; with 16 workers × 16 cores the scheduler spends more time answering HTTP than arranging jobs. The lesson is that theoretical policy comparisons are moot if the plumbing cannot keep pace.

Priority scheduling was the most surprising. I expected classic starvation for low-priority work, and we did see multi-second tail latencies for background batches. But the instrumentation also showed that high-priority bursts can overload L3 caches and elevate CPU usage enough to slow *all* jobs, not just the low-priority ones. In other words, favoring important work can still backfire if the implementation does not throttle or smooth arrivals. The fix was not algorithmic but practical: we added heap-based ready queues plus arrival threads to keep load admission smooth.

Round-robin delivered the most educational fairness story. In datasets with small quanta (1–5 ms simulated), the number of slices per job skyrocketed, which means the number of scheduler round-trips grew linearly with (jobs × slices). That is precisely the distributed version of context-switch overhead. Watching `avg_slices_per_job` climb above 400 made it tangible that fairness costs real bandwidth. I now think of fairness as a budgeted resource: every extra slice should be justified by a metric such as slowdown-based Jain's index, not just by tradition.

FIFO, while seemingly naïve, became the baseline that kept us honest. Its single-pass execution allowed us to isolate how much of the latency inflation in other policies came from coordination rather than workload variability. In the burst datasets it produced predictable queue buildup, and because workers run jobs to completion, the amount of scheduler chatter was minimal. That provided a clean control condition for debugging the more complex paths.

Another takeaway concerns automation hygiene. `run_matrix.py` originally supported a small subset of configurations, but extending it to the full Cartesian grid unlocked a more scientific workflow: edit code, rerun the grid, compare `summary.csv` snapshots. The script also taught me to treat infrastructure (starting uvicorn servers, waiting for readiness, copying CSVs) as code; copy-pasting shell commands was too brittle for 16×16 experiments. The carefully named directories—`results/replicas_X_cores_Y`—now serve as a public data package satisfying the coursework requirement for dataset sharing.

Finally, the project clarified the value of instrumentation. Prometheus endpoints and structured JSON logs let us correlate scheduler CPU usage with queue sizes and job completion rates. Without those signals the qualitative observations above would have remained anecdotes. The experience cemented the idea that "opinion" pieces about system behavior must be grounded in reproducible evidence, not just intuition.

---

### Opinia despre Algoritmul Round-Robin în Sisteme Distribuite

Building this scheduler made the gap between “algorithm on paper” and “algorithm in production” very clear. Round-Robin is easy to describe, but once we distribute it, the implementation details (locks, long-polling, request rates, queue operations, and data collection) become just as important as the scheduling policy. I initially assumed that adding machines would almost linearly reduce runtime, because more cores can run more slices at once. The plots showed that this is only partly true: performance improves, but with diminishing returns, especially when the quantum is small.

The biggest insight was that the scheduler is not “free.” Every quantum generates coordination work: workers ask for slices, then report completion. When quantum is small, the number of slices explodes, so the number of HTTP round trips explodes as well. At that point, the system becomes dominated by scheduling and communication overhead rather than “execution.” This explains why very small quanta (like 1ms) produce extremely long wall-clock runs and huge “avg_slices_per_job” values. In other words, we recreated the classic context-switch overhead effect, but at a distributed scale.

Fairness was also interesting. Smaller quanta usually improve fairness because jobs share the CPU more frequently, preventing starvation and improving short-job responsiveness. But fairness comes at a cost: more preemptions and more scheduler traffic. The project made me think about the right fairness metrics (e.g., slowdown-based Jain’s index, waiting-time Gini) and how different metrics can tell different stories depending on whether we value equality of waiting time, equality of slowdown, or tail latency.
In conclusion, I learned that “more machines” is not a universal solution: scaling requires identifying bottlenecks. If the scheduler remains centralized and each slice requires a network handshake, the scheduler becomes the limiting factor.

## Implementation Description

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

### Structuri de Date și Algoritmi de Schedulare

**FIFO (First-In-First-Out)**: Folosește `Deque[str]` pentru `ready_q` și `Dict[str, JobState]` pentru `pending_jobs` și `running_jobs`. Algoritmul procesează job-urile strict în ordinea de sosire.

**Round-Robin**: Folosește `Deque[str]` pentru `ready_q` și `List[str]` pentru `pending_ids`. Fiecare job are un `remaining_ms` care este decrementat cu `quantum_ms` la fiecare slice.

**Priority Queue**: Folosește `List[Tuple[int, int, int, str]]` (min-heap) pentru `ready_heap`, sortat după prioritate, arrival time și sequence number.

### Simularea Timpului și Speedup

Toate implementările folosesc un factor de speedup (default: 20000x) pentru a accelera simularea. Timpul real (wall time) este convertit în timp simulat folosind formula: `simulated_time = (wall_time - start_wall_time) * speedup`. Această abordare permite rularea experimentelor cu 1000+ job-uri în câteva secunde.

### Colectarea Metricilor

La finalizarea unui job, scheduler-ul calculează: `waiting_time_ms` (timp în coadă), `execution_time_ms` (timp efectiv execuție), `response_time_ms` (timp total în sistem), `slowdown` (raport între timp total și timp de serviciu), `cpu_usage_percent` și `memory_usage_mb`. Pentru Round-Robin, se colectează și `slices` (număr de quantum-uri) și `preemptions`.

### Structura Rezultatelor

Rezultatele sunt salvate în CSV-uri structurate: pentru FIFO și Priority în foldere `results/replicas_X_cores_Y/`, iar pentru Round-Robin direct în `results/`. Fiecare `results_jobs_*.csv` conține metrici per job, iar `results_run_*.csv` conține statistici agregate (mean, p50, p95, p99).

## Conclusions

Această analiză comparativă a trei algoritmi fundamentali de schedulare în sisteme distribuite demonstrează că alegerea algoritmului potrivit depinde critic de caracteristicile sarcinii de lucru și de constrângerile infrastructurii. FIFO oferă simplitate și predictibilitate, dar suferă de convoy effect și nu permite priorizare. Round-Robin asigură fairness prin quantum slicing, dar overhead-ul de coordonare crește semnificativ cu quantum-uri mici, transformând scheduler-ul într-un bottleneck. Priority Queue permite procesarea rapidă a job-urilor critice, dar poate duce la starvation și poate afecta performanța generală prin overload de cache și CPU. Rezultatele experimentale evidențiază că, în sisteme distribuite, overhead-ul infrastructurii (comunicare HTTP, sincronizare) poate domina performanța, făcând diferențele între algoritmi mai subtile decât în sisteme monoprocesor. Instrumentarea și colectarea metricilor detaliate au fost esențiale pentru înțelegerea comportamentului real al sistemelor, demonstrând că evaluarea algoritmilor de schedulare trebuie să ia în considerare atât politica de schedulare, cât și costurile infrastructurii.

## Bibliography

1. Tanenbaum, A. S., & Bos, H. (2014). *Modern Operating Systems* (4th ed.). Pearson. - Capitolul despre scheduling algorithms oferă o analiză detaliată a algoritmilor FCFS/FIFO și a impactului lor asupra performanței sistemelor.

2. Silberschatz, A., Galvin, P. B., & Gagne, G. (2018). *Operating System Concepts* (10th ed.). Wiley. - Discută despre convoy effect și despre când FIFO este sau nu este potrivit pentru diferite tipuri de sisteme.

3. Jain, R. (1991). *The Art of Computer Systems Performance Analysis: Techniques for Experimental Design, Measurement, Simulation, and Modeling*. Wiley. - Oferă metodologii pentru măsurarea fairness-ului în sisteme distribuite, inclusiv indicii Jain pentru evaluarea echității.

4. Tanenbaum, A. S., & Van Steen, M. (2017). *Distributed Systems: Principles and Paradigms* (3rd ed.). Pearson. - Analizează provocările specifice ale schedulării în sisteme distribuite, inclusiv overhead-ul de comunicare și sincronizare.
