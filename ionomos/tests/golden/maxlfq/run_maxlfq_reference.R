# MaxLFQ reference for tests/test_rollup.py (D76). Checked with iq 2.0.1 (CRAN) and diann 1.0.1
# (github.com/vdemichev/diann-rpackage, master of 2026-10-04).
# Usage (in this folder): Rscript run_maxlfq_reference.R [R libraries with iq, diann and their dependencies]
#
# Writes, per protein and sample (log2):
#   iq.tsv      iq::maxLFQ() on the protein's features x samples (log2): estimate and annotation (the components,
#               when the samples fall into more than one), plus "sum": the same profile rescaled per component so
#               its summed linear intensity equals the component's summed feature intensity (Cox et al. 2014)
#   diann.tsv   diann::diann_maxlfq() on the same long table (natural log inside, returned linear; here log2).
#               DIA-NN's solve pulls each sample weakly (1e-4) towards its most intense feature instead of fixing
#               the mean, so only the profile within a connected protein is comparable.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args, .libPaths()))
suppressPackageStartupMessages({ library(iq); library(diann); library(data.table) })

x <- read.delim("input.tsv", stringsAsFactors = FALSE, colClasses = c("character", "character", "character",
                                                                       "numeric"))
samples <- paste0("S", 1:8)
out <- NULL
for (prot in unique(x$protein)) {
  d <- x[x$protein == prot, ]
  feats <- unique(d$feature)
  X <- matrix(NA_real_, nrow = length(feats), ncol = length(samples), dimnames = list(feats, samples))
  X[cbind(match(d$feature, feats), match(d$sample, samples))] <- log2(d$intensity)
  r <- maxLFQ(X)
  est <- r$estimate
  if (length(est) == 1 && is.na(est)) est <- rep(NA_real_, length(samples))
  # the component of each sample: iq's annotation when there is more than one, else one for every quantified sample
  g <- if (nzchar(r$annotation) && r$annotation != "NA") suppressWarnings(as.integer(strsplit(r$annotation, ";")[[1]])) else
    ifelse(is.na(est), NA, 1L)
  scaled <- est
  for (k in unique(g[!is.na(g)])) {
    cols <- which(g == k & !is.na(est))
    vals <- X[, cols, drop = FALSE]
    total <- sum(2^vals[!is.na(vals)])
    scaled[cols] <- est[cols] + log2(total / sum(2^est[cols]))
  }
  out <- rbind(out, data.frame(protein = prot, sample = samples, estimate = est, sum = scaled,
                               component = g, annotation = r$annotation))
}
fmt <- function(v) ifelse(is.na(v), "NA", trimws(formatC(v, digits = 15, format = "g")))
out$estimate <- fmt(out$estimate)
out$sum <- fmt(out$sum)
write.table(out, "iq.tsv", sep = "\t", quote = FALSE, row.names = FALSE, na = "NA")

long <- data.frame(File.Name = x$sample, Protein.Names = x$protein, Precursor.Id = x$feature,
                   Precursor.Normalised = x$intensity)
dl <- diann_maxlfq(long, sample.header = "File.Name", group.header = "Protein.Names", id.header = "Precursor.Id",
                   quantity.header = "Precursor.Normalised")
dl <- dl[, intersect(samples, colnames(dl)), drop = FALSE]
res <- NULL
for (prot in rownames(dl)) {
  v <- log2(as.numeric(dl[prot, ]))
  res <- rbind(res, data.frame(protein = prot, sample = colnames(dl), estimate = fmt(v)))
}
write.table(res, "diann.tsv", sep = "\t", quote = FALSE, row.names = FALSE)
writeLines(c(paste("R", R.version$major, R.version$minor), paste("iq", packageVersion("iq")),
             paste("diann", packageVersion("diann"))), "versions.txt")
