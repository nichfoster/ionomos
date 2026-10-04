# KSEAapp reference for Ionomos' kinase activity (D79, downstream/phospho.py ksea_scores).
# Usage (from this folder): Rscript run_kseaapp.R [R library with KSEAapp]
# Checked with KSEAapp 2.0 (R 4.6.1). Inputs from make_ksea_inputs.py (made-up kinases and sites).
#   KSEA.Scores(KSData, PX, NetworKIN = FALSE)                    -> ksea_psp.tsv
#   KSEA.Scores(KSData, PX, NetworKIN = TRUE, NetworKIN.cutoff = 5) -> ksea_networkin.tsv
# Columns: KSEAapp's (Kinase.Gene, mS, Enrichment, m, z.score, p.value = one-sided, FDR over every kinase), and
# the two numbers Ionomos reports instead: p_two_sided = 2 * p.value, and fdr_min5 = BH of p_two_sided over the
# kinases with m >= 5 (ksea_min_substrates), NA for the others.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(strsplit(args[1], ":")[[1]], .libPaths()))
suppressPackageStartupMessages(library(KSEAapp))

PX <- read.delim("ksea_px.tsv", stringsAsFactors = FALSE, colClasses = c(p = "numeric", FC = "numeric"))
KS <- read.delim("ksea_ksdata.tsv", stringsAsFactors = FALSE, check.names = FALSE,
                 colClasses = c(networkin_score = "numeric", SITE_GRP_ID = "character", SUB_GENE_ID = "character"))
for (case in list(list("ksea_psp.tsv", FALSE, 5), list("ksea_networkin.tsv", TRUE, 5))) {
  s <- KSEA.Scores(KS, PX, NetworKIN = case[[2]], NetworKIN.cutoff = case[[3]])
  s$m <- as.integer(s$m)
  s$p_two_sided <- 2 * s$p.value
  s$fdr_min5 <- NA_real_
  keep <- s$m >= 5
  s$fdr_min5[keep] <- p.adjust(s$p_two_sided[keep], method = "BH")
  write.table(format(s, digits = 17), case[[1]], sep = "\t", quote = FALSE, row.names = FALSE)
  cat(case[[1]], nrow(s), "kinases,", sum(keep), "with m >= 5\n")
}
cat("KSEAapp", as.character(packageVersion("KSEAapp")), "\n")
