# MSstatsTMT reference for tests/test_plexes.py (checked with MSstatsTMT 2.20.0, MSstatsConvert 1.22.1).
# Usage (in this folder): Rscript run_msstatstmt.R [R library with MSstatsTMT]
# Fractions are combined the way MSstatsTMT's converters do it (MSstatsConvert::MSstatsBalancedDesign with
# handle_fractions = TRUE), then proteinSummarization(method = "MedianPolish"), with and without the reference
# (Norm) normalisation between runs. Norm and Empty channels are left in, so every step can be compared.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages({ library(MSstatsTMT); library(data.table) })

raw <- data.table::as.data.table(read.csv("input.csv", stringsAsFactors = FALSE))
data.table::setnames(raw, "Charge", "PrecursorCharge")
merged <- MSstatsConvert::MSstatsBalancedDesign(raw, c("PeptideSequence", "PrecursorCharge"), TRUE, TRUE, "zero_to_na")
merged <- data.table::as.data.table(unclass(merged))
data.table::setnames(merged, "PrecursorCharge", "Charge")

run <- function(reference_norm) {
  out <- proteinSummarization(as.data.frame(merged), method = "MedianPolish", global_norm = TRUE,
                              reference_norm = reference_norm, remove_norm_channel = FALSE,
                              remove_empty_channel = FALSE, MBimpute = FALSE, use_log_file = FALSE, verbose = FALSE)
  p <- out$ProteinLevelData
  p <- p[!is.na(p$Abundance), c("Protein", "Run", "Channel", "Condition", "Abundance")]
  p[order(p$Protein, p$Run, p$Channel), ]
}
write.csv(run(TRUE), "expected.csv", row.names = FALSE)
write.csv(run(FALSE), "expected_no_reference_norm.csv", row.names = FALSE)
cat("MSstatsTMT", as.character(packageVersion("MSstatsTMT")), "\n")
