# limma + splines reference for spline time courses (timecourse.py, D77; limma User's Guide 9.6.2).
# Usage (from this folder): python make_spline_inputs.py && Rscript run_spline_reference.R [R library with limma]
# Checked with R 4.6.1 (splines) and limma 3.68.5. Writes:
#   ns_basis.tsv   splines::ns() bases (and predict() on a grid) for several time vectors and df
#   sp_<case>.tsv  per feature: each series' moderated F on its spline coefficients (its own model: the series as
#                  a level + ns(hours, df), every other condition its own mean, plus the block), the fitted change
#                  from 0 h at each time point, and the interaction F of Drug against DMSO from limma's own
#                  ~Group * ns(time) parameterisation (topTable on the interaction coefficients).
args <- commandArgs(trailingOnly = TRUE)
if (length(args) > 0) .libPaths(c(args[1], .libPaths()))
suppressPackageStartupMessages({ library(limma); library(splines) })

# ---- the basis itself
cases <- list(
  list("exp_df1", rep(c(0, 0.5, 1, 2, 4, 8, 24, 48), each = 3), 1),
  list("exp_df3", rep(c(0, 0.5, 1, 2, 4, 8, 24, 48), each = 3), 3),
  list("exp_df4", rep(c(0, 0.5, 1, 2, 4, 8, 24, 48), each = 3), 4),
  list("exp_df6", rep(c(0, 0.5, 1, 2, 4, 8, 24, 48), each = 3), 6),
  list("unequal_df4", c(0, 0, 0, 1, 1, 2, 4, 4, 4, 4, 8, 24, 24), 4),
  list("shoved_df4", c(0, 0, 0, 0, 0, 0, 1, 2, 3), 4),
  list("tied_df4", c(0, 1, 1, 1, 1, 1, 1, 2), 4),
  list("days_df5", c(0, 1, 2, 3, 5, 7, 10, 14, 21, 28) * 24, 5))
out <- NULL
for (cs in cases) {
  b <- suppressWarnings(ns(cs[[2]], df = cs[[3]]))
  grid <- seq(min(cs[[2]]), max(cs[[2]]), length.out = 7)
  p <- predict(b, grid)
  long <- function(m, what, x) data.frame(case = cs[[1]], what = what, x = rep(x, ncol(m)),
                                          row = rep(seq_len(nrow(m)), ncol(m)), col = rep(seq_len(ncol(m)), each = nrow(m)),
                                          value = as.vector(m))
  out <- rbind(out, long(unclass(b)[, , drop = FALSE], "basis", cs[[2]]), long(p, "predict", grid))
  k <- attr(b, "knots")
  if (length(k)) out <- rbind(out, data.frame(case = cs[[1]], what = "knot", x = NA, row = seq_along(k), col = 0,
                                              value = k))
}
write.table(format(out, digits = 17), "ns_basis.tsv", sep = "\t", quote = FALSE, row.names = FALSE)

# ---- the tests
sd <- read.delim("sp_samples.tsv", stringsAsFactors = FALSE)
lev <- function(v) factor(v, levels = unique(v))
replicate <- lev(as.character(sd$replicate))
times <- unique(sd$time[sd$series == "Drug"])
ind <- function(v) as.numeric(v)
design_for <- function(members, df, block) {
  inm <- sd$series %in% members
  B <- matrix(0, nrow(sd), df)
  B[inm, ] <- ns(sd$time[inm], df = df)
  others <- unique(sd$condition[!inm])
  X <- sapply(others, function(c) ind(sd$condition == c))
  for (s in members) X <- cbind(X, ind(sd$series == s), B * ind(sd$series == s))
  if (block) X <- cbind(X, model.matrix(~replicate)[, -1, drop = FALSE])
  colnames(X) <- make.names(seq_len(ncol(X)))
  list(X = X, cols = length(others) + 1 + seq_len(df), basis = ns(sd$time[inm], df = df))
}
run <- function(name, y, df, block) {
  out <- data.frame(ID = rownames(y))
  for (s in c("Drug", "DMSO")) {
    d <- design_for(s, df, block)
    fit <- eBayes(lmFit(y, d$X))
    f <- topTable(fit, coef = d$cols, number = Inf, sort.by = "none")
    out[[paste0(s, "_F")]] <- f$F; out[[paste0(s, "_P")]] <- f$P.Value; out[[paste0(s, "_adjP")]] <- f$adj.P.Val
    rel <- predict(d$basis, times) - matrix(predict(d$basis, times[1]), length(times), df, byrow = TRUE)
    fc <- fit$coefficients[, d$cols, drop = FALSE] %*% t(rel)
    for (k in 2:length(times)) out[[paste0(s, "_fc", k - 1)]] <- fc[, k]
  }
  # Drug against DMSO: limma's ~Group * ns(time) on the two series, the other conditions their own means
  inm <- sd$series %in% c("Drug", "DMSO")
  X <- ns(sd$time[inm], df = df)
  Group <- factor(sd$series[inm], levels = c("DMSO", "Drug"))
  G <- model.matrix(~Group * X)
  full <- matrix(0, nrow(sd), ncol(G)); full[inm, ] <- G
  others <- unique(sd$condition[!inm])
  D <- cbind(full, sapply(others, function(c) ind(sd$condition == c)))
  if (block) D <- cbind(D, model.matrix(~replicate)[, -1, drop = FALSE])
  colnames(D) <- make.names(seq_len(ncol(D)))
  fi <- topTable(eBayes(lmFit(y, D)), coef = (2 + df + 1):(2 + 2 * df), number = Inf, sort.by = "none")
  out$inter_F <- fi$F; out$inter_P <- fi$P.Value; out$inter_adjP <- fi$adj.P.Val
  write.table(format(out, digits = 15), paste0("sp_", name, ".tsv"), sep = "\t", quote = FALSE, row.names = FALSE)
}
full <- as.matrix(read.delim("sp_matrix.tsv", row.names = 1, check.names = FALSE))
miss <- as.matrix(read.delim("sp_matrix_missing.tsv", row.names = 1, check.names = FALSE))
run("df4", full, 4, FALSE)
run("df4_block", full, 4, TRUE)
run("df3_missing", miss, 3, FALSE)
cat("R", as.character(getRversion()), "limma", as.character(packageVersion("limma")), "\n")
