# Round-trip test of the R encoder. Run: Rscript --vanilla test_encode.R
# Exits with status 1 on any failure. Optional argument: a path to write
# the encoded test table to, for the cross-language test in tests/.

here <- dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(FALSE), value = TRUE))))
source(file.path(here, "modelcheck_encode.R"))
args <- commandArgs(trailingOnly = TRUE)

awkward <- c(
  "0000000000000001",  # smallest subnormal
  "000fffffffffffff",  # largest subnormal
  "0010000000000000",  # smallest normal
  "8000000000000000",  # -0.0
  "0000000000000000",  # 0.0
  "7fefffffffffffff",  # largest finite double
  "ffefffffffffffff",  # its negative
  "7ff0000000000000",  # Inf
  "fff0000000000000",  # -Inf
  "7ff00000000007a2",  # R's NA
  "7ff8000000000000",  # R's NaN
  "7ff8000000000001",  # a NaN with a payload
  "fff8000000000000",  # a NaN with the sign bit set
  "3fb999999999999a"   # 0.1
)
x <- mc_unhex(awkward)
stopifnot(identical(mc_hex(x), awkward))
stopifnot(is.na(x[10]), !is.nan(x[10]), is.nan(x[11]))
stopifnot(identical(mc_hex(-0), "8000000000000000"), identical(mc_hex(NA_real_), "7ff00000000007a2"))
stopifnot(identical(mc_hex(.Machine$double.xmax), "7fefffffffffffff"))
stopifnot(identical(mc_hex(4.9406564584124654e-324), "0000000000000001"))

df <- data.frame(country = c("c1", "c2", NA), n = c(1L, NA, 3L), ok = c(TRUE, NA, FALSE),
                 value = c(0.1, NA, -0), stringsAsFactors = FALSE)
t <- mc_table(df)
stopifnot(identical(vapply(t$columns, function(c) c$type, ""), c("string", "integer", "logical", "double")))
stopifnot(is.null(t$columns[[1]]$values[[3]]), is.null(t$columns[[2]]$values[[2]]))
stopifnot(identical(unlist(t$columns[[4]]$values), c("3fb999999999999a", "7ff00000000007a2", "8000000000000000")))
stopifnot(identical(mc_scalar(22L), list(integer = 22L)), is.null(mc_scalar(NA_integer_)))
stopifnot(identical(mc_scalar(NA_real_), list(double = "7ff00000000007a2")))

if (length(args) >= 1) {
  mc_write_reference(list(awkward = mc_table(data.frame(bits = awkward, value = x, stringsAsFactors = FALSE)),
                          mixed = t),
                     list(name = "encoder test", version = "1"), args[[1]])
}
cat("R encoder: all round-trip checks passed\n")
