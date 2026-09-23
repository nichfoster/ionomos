"""Builds the inputs for the R-vs-Python golden tests. Run once; outputs are committed.

    python make_inputs.py && Rscript run_r_scripts.R <scratch R library>
"""
import random

rng = random.Random(42)
H = "[561.3387]"
prefixes = ["isoDTB_EJQ_2_027", "isoDTB_EJQ_2_028"]
ratio_cols = [f"{p}_{r} Log2 Ratio HL" for p in prefixes for r in (1, 2, 3)]
header = ["Peptide Sequence", "Light Modified Peptide", "Heavy Modified Peptide", "Start", "End", "Protein",
          "Protein ID", "Entry Name", "Gene", "Protein Description", *ratio_cols]
peptides = [
    # (sequence, light modified, start, protein, id, entry, gene, desc)
    ("AKCLLR", f"AKC{H}LLR", 10, "sp|P1|A_HUMAN", "P1", "A_HUMAN", "GENEA", "Protein A"),
    ("KCLLRE", f"KC{H}LLRE", 11, "sp|P1|A_HUMAN", "P1", "A_HUMAN", "GENEA", "Protein A"),     # same site as above
    ("CPEPCK", f"C{H}PEPC{H}K", 50, "sp|P1|A_HUMAN", "P1", "A_HUMAN", "GENEA", "Protein A"),  # two sites
    ("MCDEK", f"M[15.9949]C{H}DEK", 1, "sp|P2|B_HUMAN", "P2", "B_HUMAN", "GENEB", "Protein B"),
    ("ACDEK", f"n[42.0106]AC{H}DEK", 5, "sp|P2|B_HUMAN", "P2", "B_HUMAN", "GENEB", "Protein B"),  # N-term quirk
    ("PLAIN", "PLAIN", 70, "sp|P2|B_HUMAN", "P2", "B_HUMAN", "GENEB", "Protein B"),              # no label
    ("GGCKK", f"GGC{H}KK", 200, "sp|P3|C_HUMAN", "P3", "C_HUMAN", "", "Protein C, no gene"),
    ("QQCQQ", f"QQC{H}QQ", 30, "sp|P4|D_HUMAN", "P4", "D_HUMAN", "GENED", "Protein D"),
]
for _i in range(40):  # bulk
    k = rng.randint(3, 9)
    seq = "".join(rng.choice("ADEFGHIKLMNPQRSTVWY") for _ in range(k)) + "C" + "".join(rng.choice("ADEFGHIKLMNPQRSTVWY") for _ in range(3))
    mod = seq.replace("C", "C" + H, 1)
    prot = rng.randint(5, 25)
    peptides.append((seq, mod, rng.randint(1, 900), f"sp|Q{prot}|P{prot}_HUMAN", f"Q{prot}", f"P{prot}_HUMAN",
                     f"G{prot}", f"Protein {prot}"))

with open("isodtb_label_quant.tsv", "w", encoding="utf-8", newline="") as fh:
    fh.write("\t".join(header) + "\n")
    for n, (seq, mod, start, prot, pid, entry, gene, desc) in enumerate(peptides):
        vals = []
        for c in ratio_cols:
            if rng.random() < 0.15 or (n == 7 and "028" in c):  # missing values; one site all-NA for sample 028
                vals.append("")
            else:
                vals.append(f"{rng.gauss(0.3, 1.1):.4f}")
        fh.write("\t".join([seq, mod, mod.replace(H, "[567.3462]"), str(start), str(start + len(seq) - 1), prot, pid,
                            entry, gene, desc, *vals]) + "\n")

# TMT abundance header: mixes valid condition_1_channel names with ones the regex drops
tmt_cols = ["Index", "NumberPSM", "ProteinID", "MaxPepProb", "ReferenceIntensity",
            "DMSO_1_126", "DMSO_1_127N", "DMSO_1_127C", "Drug_1_128N", "Drug_1_128C", "Drug_1_129N",
            "Pool_1_131", "bad-name_1_130N", "DMSO_2_130C", "Veh_1_134N"]
with open("tmt_abundance_gene_MD.tsv", "w", encoding="utf-8", newline="") as fh:
    fh.write("\t".join(tmt_cols) + "\n")
    fh.write("\t".join(["GENEA", "12", "P1", "1.0", "20.5", *["0.1"] * (len(tmt_cols) - 5)]) + "\n")
