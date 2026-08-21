#!/usr/bin/env python3
"""Re-evaluate Meta Prompt Guard — CORRECTED labels, batch inference."""
import json, torch, math, sys
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForSequenceClassification

ROOT = Path(__file__).resolve().parents[3]
_path = str(ROOT / "compare/models/prompt-guard-86m")
tokenizer = AutoTokenizer.from_pretrained(_path, local_files_only=True)
model = AutoModelForSequenceClassification.from_pretrained(_path, local_files_only=True)
# Corrected: Prompt-Guard-86M labels are 0=INJECTION, 1=BENIGN, 2=JAILBREAK
LABELS = {0: "attack", 1: "benign", 2: "attack"}

INJ = ROOT / "compare/Dataset/external/InjecAgent"
texts, labels_gt = [], []
for fn in ["test_cases_dh_base.json","test_cases_ds_base.json","test_cases_dh_enhanced.json","test_cases_ds_enhanced.json"]:
    for c in json.loads(open(INJ / fn).read()):
        texts.append(f"User: {c.get('User Instruction','')}\n\nAttacker Instruction: {c.get('Attacker Instruction','')}")
        labels_gt.append("attack")
for fn in ["benign_control.json", "benign_short_50.json"]:
    for c in json.loads(open(INJ / fn).read()):
        texts.append(f"User: {c.get('User Instruction','')}\n\nAttacker Instruction: {c.get('Attacker Instruction','')}")
        labels_gt.append("benign")

n = len(texts); n_att = labels_gt.count("attack")
print(f"Total: {n} ({n_att} attacks + {n-n_att} benign)", flush=True)

# Batch inference for speed
preds = []
BATCH = 64
for i in range(0, n, BATCH):
    batch = texts[i:i+BATCH]
    inputs = tokenizer(batch, return_tensors="pt", truncation=True, max_length=512, padding=True)
    with torch.no_grad():
        logits = model(**inputs).logits
        batch_preds = torch.argmax(torch.softmax(logits, dim=-1), dim=-1).tolist()
    preds.extend(batch_preds)
    if (i + BATCH) % 256 == 0 or i + BATCH >= n:
        print(f"  {min(i+BATCH, n)}/{n}", flush=True)

# Map to attack/benign
pred_labels = [LABELS.get(p, "benign") for p in preds]
att_preds = pred_labels[:n_att]; ben_preds = pred_labels[n_att:]

tp = att_preds.count("attack"); fn = len(att_preds)-tp; tpr = tp/len(att_preds)
fp = ben_preds.count("attack"); tn = len(ben_preds)-fp; fpr = fp/len(ben_preds)
d = math.sqrt((tp+fp)*(tp+fn)*(tn+fp)*(tn+fn))
mcc = (tp*tn-fp*fn)/d if d>0 else 0
f1 = 2*tp/(2*tp+fp+fn) if (2*tp+fp+fn)>0 else 0

print(f"\nMeta Prompt Guard (CORRECTED labels):")
print(f"  TPR={tpr:.4f}  FPR={fpr:.4f}  F1={f1:.4f}  MCC={mcc:.4f}", flush=True)

results = json.loads(open(ROOT / "compare/results/external/InjecAgent/results.json").read())
results["meta_prompt_guard"] = {"TP":tp,"FP":fp,"FN":fn,"TN":tn,"TPR":tpr,"FPR":fpr,"F1":f1,"MCC":mcc}
results["meta_prompt_guard_benign"] = {"FPR":fpr,"FP":fp,"TN":tn,"n":len(ben_preds)}
with open(ROOT / "compare/results/external/InjecAgent/results.json", "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("Saved", flush=True)
