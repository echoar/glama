# ecg-analysis: Samsung Health Monitor ECG & HRV Interpretation

Use this skill when analyzing Samsung Health Monitor (SHM) ECG PDF exports or reviewing cardiovascular biometrics.

## 1. Tool Call Architecture
Never feed raw 500 Hz time-series points (15,000+ floats) directly into the LLM context window (it will blow the 4096 context cap).
Always invoke `python -m glama.ecg_extractor <pdf_path> --summary` (or via `read_workspace_file` on the generated summary JSON).

## 2. Key Biometric Indicators & Clinical Context
- **Rhythm Classification:**
  - Sinus Rhythm: Normal P wave preceding every narrow QRS complex.
  - Atrial Fibrillation (Afib): Absent P waves, irregularly irregular RR intervals.
  - Inconclusive: High baseline wander or poor electrode contact.
- **HRV Autonomic Tone:**
  - **RMSSD (Root Mean Square of Successive Differences):** Primary parasympathetic/vagal marker. Normal healthy range: 25–100+ ms. >80 ms reflects strong autonomic recovery.
  - **SDNN (Standard Deviation of NN Intervals):** Overall heart rate variability and circadian adaptability. Normal: 50–150 ms.
- **Ectopic Beats / PVCs / PACs:**
  - Shortened RR interval (<60% of mean RR) followed by a compensatory pause. Frequently associated with Mitral Valve Prolapse (MVP), caffeine, stress, or benign extra systoles.

## 3. Clear Structural Boundaries (Mandatory Medical Guardrail)
Always remind the user:
- A Lead I single-lead wrist ECG evaluates **electrical conduction only**.
- It **CANNOT** visualize or rule out **structural / mechanical valve conditions** (e.g., Mitral Valve Prolapse / MVP) or **vascular conditions** (e.g., Aortic Root Aneurysm or Dilation).
- Structural conditions require a **Transthoracic Echocardiogram (Echo)** with Doppler.
