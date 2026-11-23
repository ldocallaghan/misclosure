# modelcheck reference encoder for R.
#
# Doubles are the 16 hexadecimal digits of their big-endian IEEE 754 bit
# pattern, so every bit crosses, NA and NaN payloads included. Other values
# are written as JSON. Needs jsonlite.
#
#   source("modelcheck_encode.R")
#   values <- list(run1 = list(info = list(iterations = mc_scalar(22L)),
#                              tables = list(welfare = mc_table(dt))))
#   mc_write_reference(values, list(name = "KITE", version = "26.10"), "reference.json")

mc_hex <- function(x) {
  if (length(x) == 0L) return(character())
  raw <- writeBin(as.double(x), raw(), size = 8L, endian = "big")
  hex <- sprintf("%02x", as.integer(raw))
  vapply(seq_along(x), function(i) paste(hex[(8L * i - 7L):(8L * i)], collapse = ""), character(1))
}

mc_unhex <- function(h) {
  stopifnot(is.character(h), all(grepl("^[0-9a-f]{16}$", h)))
  bytes <- as.raw(strtoi(substring(rep(h, each = 8L), seq(1L, 15L, 2L), seq(2L, 16L, 2L)), 16L))
  readBin(bytes, "double", n = length(h), size = 8L, endian = "big")
}

mc_column <- function(name, x) {
  if (is.factor(x)) x <- as.character(x)
  type <- if (is.character(x)) "string" else if (is.double(x)) "double"
    else if (is.integer(x)) "integer" else if (is.logical(x)) "logical"
    else stop("unsupported column type: ", class(x)[1])
  values <- if (type == "double") as.list(mc_hex(x))
    else lapply(x, function(v) if (is.na(v)) NULL else v)
  list(name = name, type = type, values = values)
}

mc_table <- function(df) {
  list(columns = lapply(names(df), function(n) mc_column(n, df[[n]])))
}

mc_scalar <- function(x) {
  if (is.null(x) || length(x) == 0L) return(NULL)
  x <- x[[1]]
  if (is.double(x)) return(list(double = mc_hex(x)))
  if (is.na(x)) return(NULL)
  if (is.integer(x)) return(list(integer = x))
  if (is.logical(x)) return(list(logical = x))
  list(string = as.character(x))
}

mc_reference <- function(values, model) {
  list(modelcheck_reference = 1L, model = model, values = values)
}

mc_write_json <- function(x, path) {
  writeLines(jsonlite::toJSON(x, auto_unbox = TRUE, null = "null", digits = NA), path, useBytes = TRUE)
}

mc_write_reference <- function(values, model, path) {
  mc_write_json(mc_reference(values, model), path)
}
