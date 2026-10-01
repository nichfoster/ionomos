# limma reference for the time-course tests (timecourse.py): F over time, the linear trend, the interaction F.
# Usage (from this folder): Rscript run_timecourse_reference.R [R library with limma]
# Checked with limma 3.68.5. Factor levels are in order of appearance, as Ionomos builds them.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages(library(limma))
sd <- read.delim("tc_samples.tsv", stringsAsFactors = FALSE)
lev <- function(v) factor(v, levels = unique(v))
condition <- lev(sd$condition)
replicate <- lev(as.character(sd$replicate))
times <- c("0h", "1h", "4h", "24h")
over <- function(s) paste0("condition", s, "_", times[-1], " - condition", s, "_0h")
trend <- function(s) paste(sprintf("%s*condition%s_%s", c(-1.5, -0.5, 0.5, 1.5), s, times), collapse = " + ")
inter <- paste0("(conditionDrug_", times[-1], " - conditionDrug_0h) - (conditionDMSO_", times[-1], " - conditionDMSO_0h)")
run <- function(name, y, design) {
  fit <- lmFit(y, design)
  one <- function(cn) eBayes(contrasts.fit(fit, makeContrasts(contrasts = cn, levels = design)))
  out <- data.frame(ID = rownames(y))
  for (s in c("Drug", "DMSO")) {
    f <- suppressMessages(topTableF(one(over(s)), number = Inf, sort.by = "none"))
    tr <- topTable(one(trend(s)), coef = 1, number = Inf, sort.by = "none")
    out[[paste0(s, "_F")]] <- f$F; out[[paste0(s, "_P")]] <- f$P.Value; out[[paste0(s, "_adjP")]] <- f$adj.P.Val
    for (k in 1:3) out[[paste0(s, "_fc", k)]] <- f[[k]]
    out[[paste0(s, "_trend_t")]] <- tr$t; out[[paste0(s, "_trend_P")]] <- tr$P.Value
    out[[paste0(s, "_trend_adjP")]] <- tr$adj.P.Val
  }
  fi <- suppressMessages(topTableF(one(inter), number = Inf, sort.by = "none"))
  out$inter_F <- fi$F; out$inter_P <- fi$P.Value; out$inter_adjP <- fi$adj.P.Val
  write.table(format(out, digits = 15), paste0("tc_", name, ".tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
}
full <- as.matrix(read.delim("tc_matrix.tsv", row.names = 1, check.names = FALSE))
miss <- as.matrix(read.delim("tc_matrix_missing.tsv", row.names = 1, check.names = FALSE))
X0 <- model.matrix(~0 + condition); colnames(X0) <- make.names(colnames(X0))
Xb <- model.matrix(~0 + condition + replicate); colnames(Xb) <- make.names(colnames(Xb))
run("plain", full, X0)
run("plain_missing", miss, X0)
run("block", full, Xb)
cat("limma", as.character(packageVersion("limma")), "\n")
