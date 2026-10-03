# limma reference for IRS on the plex means (D71): three TMT plexes of 3 DMSO + 3 Drug channels, no reference channel.
# Usage (from this folder): Rscript run_tmt_sum_reference.R [R library with limma]
# Checked with limma 3.68.5. What Ionomos does with irs: sum, normalize: median, in base R + limma:
#   IRS        per protein and plex, log2 of the linear mean of the plex's channels when every channel has a value
#              (pwilmart's plex-sum IRS without a pool); each plex moved to the mean of those over the plexes; a
#              plex without all its channels becomes NA for the protein; a protein with no complete plex is left
#              as it was (plex.normalise)
#   filter     FragPipe-Analyst filter_by_condition: a value in >= 50 % of the samples of at least one condition
#   normalise  median centring (FragPipeAnalystR MD_normalization, moved to the median of the sample medians)
#   impute     none (TMT)
#   model      ~0 + condition, Drug - DMSO, the coefficient NA where a group has fewer than 2 values (min_valid)
#   df         the residual df of every protein reduced by (the plexes it was scaled in - 1), its residual variance
#              rescaled to the same residual sum of squares, before eBayes (plex.df_spent, fpa.spend_df): each
#              plex's level was estimated from the channels that are then tested
# Also written: the same without the df step (tmt_sum_limma_plain.tsv), to show what it changes.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(limma))

raw <- as.matrix(read.delim("tmt_sum_matrix.tsv", row.names = 1, check.names = FALSE))
sd_ <- read.delim("tmt_sum_samples.tsv", stringsAsFactors = FALSE)
cond <- sd_$condition[match(colnames(raw), sd_$sample)]
plex <- sd_$plex[match(colnames(raw), sd_$sample)]
plexes <- unique(plex)
w <- function(df, f) write.table(format(df, digits = 15), f, sep = "\t", quote = FALSE, row.names = FALSE)

# ---- IRS on the plex means
x <- raw
for (i in seq_len(nrow(x))) {
  r <- sapply(plexes, function(p) {
    v <- raw[i, plex == p]
    if (all(!is.na(v))) log2(mean(2^v)) else NA
  })
  if (all(is.na(r))) next
  g <- mean(r, na.rm = TRUE)
  for (p in plexes) {
    cols <- plex == p
    x[i, cols] <- if (is.na(r[p])) NA else raw[i, cols] + g - r[p]
  }
}
# ---- filter_by_condition(50 %)
present <- !is.na(x)
keep <- apply(present, 1, function(p) any(tapply(p, cond, mean) >= 0.5 - 1e-12))
x <- x[keep, , drop = FALSE]
# ---- median normalisation
meds <- apply(x, 2, median, na.rm = TRUE)
x <- sweep(x, 2, meds) + median(meds)
x <- x[rowSums(!is.na(x)) > 0, , drop = FALSE]
w(data.frame(ID = rownames(x), x, check.names = FALSE), "tmt_sum_processed.tsv")

# ---- the plexes each protein was scaled in: those with every channel measured
complete <- sapply(plexes, function(p) rowSums(is.na(x[, plex == p, drop = FALSE])) == 0)
spent <- pmax(rowSums(complete) - 1, 0)
w(data.frame(ID = rownames(x), spent = spent), "tmt_sum_spent.tsv")

priors <- NULL
run <- function(name, correct) {
  condition <- factor(cond)
  design <- model.matrix(~ 0 + condition)
  colnames(design) <- gsub("condition", "", colnames(design))
  fit <- lmFit(x, design = design)
  cf <- contrasts.fit(fit, makeContrasts(contrasts = "Drug - DMSO", levels = design))
  na <- rowSums(!is.na(x[, cond == "Drug", drop = FALSE]))
  nb <- rowSums(!is.na(x[, cond == "DMSO", drop = FALSE]))
  cf$coefficients[!(na >= 2 & nb >= 2), 1] <- NA
  if (correct) {
    d <- cf$df.residual
    n <- ifelse(d > 0 & spent > 0, pmax(d - spent, 0), d)
    s2 <- ifelse(d > 0 & spent > 0, cf$sigma^2 * d / n, cf$sigma^2)
    s2[n == 0] <- NA
    cf$sigma <- sqrt(s2)
    cf$df.residual <- n
  }
  eB <- eBayes(cf)
  priors <<- rbind(priors, data.frame(case = name, df.prior = format(eB$df.prior, digits = 15),
                                      s2.prior = format(eB$s2.prior, digits = 15)))
  tt <- topTable(eB, sort.by = "none", adjust.method = "BH", coef = 1, number = Inf, confint = TRUE)
  w(data.frame(comparison = "Drug vs DMSO", ID = rownames(tt), diff = tt$logFC, CI.L = tt$CI.L, CI.R = tt$CI.R,
               t = tt$t, p.val = tt$P.Value, p.adj = tt$adj.P.Val, check.names = FALSE),
    paste0("tmt_sum_limma_", name, ".tsv"))
}
run("corrected", TRUE)
run("plain", FALSE)
w(priors, "tmt_sum_priors.tsv")
cat("limma", as.character(packageVersion("limma")), "\n")
