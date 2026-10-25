# Project Zero Dawn

A seeded, testable simulation of GAIA's terraforming network from the Horizon series
(Zero Dawn / Forbidden West). Pure Python 3.9+, standard library only. A full run takes
about 0.2 s.

```bash
cd "Project Zero Dawn"
python Root_Kernel.py                           # NOMINAL
python Root_Kernel.py -s CANON -v               # Faro sabotage + rogue HEPHAESTUS + HADES
python Root_Kernel.py -s HADES_ROGUE --no-veto  # GAIA cannot stop HADES
python Root_Kernel.py --sweep 50 -s CANON       # Monte Carlo over 50 seeds
python Root_Kernel.py --json report.json --csv timeline.csv
python -m unittest discover -s tests
```

## Architecture

```
Root_Kernel  -> builds Context(seed, clock, config), installs ALOY as the bus
GAIA         -> phase plan, gates, extensions, stall detection, veto, signed resets
ALOY         -> heap-ordered pub/sub (CRITICAL first, FIFO within a priority) + inboxes
subsystems   -> execute(dt, env) / step(dt, env) / health() in [0, 1]
```

Subsystems never import each other. GAIA turns their `health()` values into an
environment vector `env` and hands it back to everyone, so the coupling is explicit.
Integration is first-order operator splitting: the phase owner advances `dt`, then every
already-started subsystem advances `dt` in the background.

## Models

| Subsystem | Model |
|---|---|
| MINERVA | Real salted SHA-256 brute force over N = 16^L, E[attempts] = (N+1)/2, t = (a/N) T_max. Tower cap fraction f = (1 - cos(d/R))/2, n_req = ceil(ln(1-C*)/ln(1-f)), verified by Monte Carlo on the sphere. Firewall probes ~ Poisson. |
| AETHER | X(t) = X* + (X0 - X*) e^(-t/tau), tau = tau0/(1 + n/n_half), toxin tau further divided by (1 + beta f_precip). Health = geometric mean of per-variable progress. |
| POSEIDON | Relaxation of toxin, DO, salinity; pH via [H+] = 10^-pH, so pH climbs linearly at 1/(2.303 tau_pH) per year far from target. Processors split by largest remainder on sqrt(V) with freshwater priority. |
| DEMETER | Seed viability v = e^(-ln2 t / (c t_half)). Coupled soil S and cover A: dS/dt = (k_w + k_c A/K + k_p P)(1-S), dA/dt = rA(1-A/K). Soil thresholds gate succession. |
| ARTEMIS | Three-guild Lotka-Volterra with logistic prey (RK4). Lindeman score exp(-abs(log10((upper/lower)/0.1))). Species viable when N >= MVP. |
| ELEUTHIA | Stratified founder draw, Ne = 4 Nm Nf/(Nm+Nf), H_g/H_0 = (1 - 1/2Ne)^g, Simpson D. Age-structured Cradles, release at 18, tribes with weak Allee logistic growth suppressed by machine threat. |
| APOLLO | (n, k) erasure coding: P_rec = sum_{i>=k} C(n,i) p^i (1-p)^(n-i). Faro sabotage samples this, about 27 % survives with defaults. |
| HEPHAESTUS | N(t+dt) = N e^(-dt/L) + built, build split by largest remainder. Salvage EMA S = (1-a)S + a x triggers autonomy and combat chassis. |
| HADES | S = 0.25 atm + 0.20 hydro + 0.25 flora + 0.20 fauna + 0.10 humans; reset only after W consecutive failures and an HMAC-SHA256 signature from GAIA. Corruption bypasses verification and starts a countdown. |

## Scenarios

`NOMINAL`, `FARO_SABOTAGE`, `HADES_ROGUE`, `HEPHAESTUS_ROGUE`, `CANON` (all three).
Add your own in `Root_Kernel.SCENARIOS` as `(when, Phase, callable)` hooks.

Not affiliated with Guerrilla Games or Sony. Licensed under Apache 2.0.
