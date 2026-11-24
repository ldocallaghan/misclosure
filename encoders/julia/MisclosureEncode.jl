# misclosure reference encoder for Julia. No dependencies outside Base.
#
# Doubles are the 16 hexadecimal digits of their big-endian IEEE 754 bit
# pattern. `missing` in a double column is written as R's NA bit pattern;
# in any other column as JSON null.
#
#   include("MisclosureEncode.jl"); using .MisclosureEncode
#   t = mcl_table(["country" => ["c1", "c2"], "value" => [1.05, 0.98]])
#   mcl_write_reference("reference.json", ["run1" => ["tables" => ["welfare" => t]]],
#                      ["name" => "mymodel", "version" => "1.0"])

module MisclosureEncode

export mcl_hex, mcl_unhex, mcl_column, mcl_table, mcl_scalar, mcl_reference, mcl_json, mcl_write_reference

const R_NA_HEX = "7ff00000000007a2"

mcl_hex(x::Float64) = string(reinterpret(UInt64, x); base = 16, pad = 16)
mcl_hex(x::Real) = mcl_hex(Float64(x))

function mcl_unhex(h::AbstractString)
    occursin(r"^[0-9a-f]{16}$", h) || throw(ArgumentError("not 16 lower-case hexadecimal digits: $h"))
    reinterpret(Float64, parse(UInt64, h; base = 16))
end

"""A JSON object that keeps its key order: a vector of pairs."""
const Obj = Vector{Pair{String,Any}}

function column_type(values)
    present = [v for v in values if v !== missing && v !== nothing]
    isempty(present) && return "double"
    all(v -> v isa Bool, present) && return "logical"
    all(v -> v isa Integer && !(v isa Bool), present) && return "integer"
    all(v -> v isa AbstractFloat, present) && return "double"
    all(v -> v isa AbstractString, present) && return "string"
    throw(ArgumentError("a column must hold one type: doubles, integers, logicals or strings"))
end

function mcl_column(name::AbstractString, values::AbstractVector; type::Union{Nothing,String} = nothing)
    t = type === nothing ? column_type(values) : type
    enc = if t == "double"
        Any[(v === missing || v === nothing) ? R_NA_HEX : mcl_hex(v) for v in values]
    else
        Any[(v === missing) ? nothing : v for v in values]
    end
    Obj(["name" => String(name), "type" => t, "values" => enc])
end

function mcl_table(columns::AbstractVector{<:Pair})
    length(unique(length(last(c)) for c in columns)) <= 1 ||
        throw(ArgumentError("every column of a table must have the same length"))
    Obj(["columns" => Any[mcl_column(first(c), last(c)) for c in columns]])
end

function mcl_scalar(x)
    (x === missing || x === nothing) && return nothing
    x isa Bool && return Obj(["logical" => x])
    x isa Integer && return Obj(["integer" => x])
    x isa AbstractFloat && return Obj(["double" => mcl_hex(x)])
    x isa AbstractString && return Obj(["string" => String(x)])
    throw(ArgumentError("cannot encode $(typeof(x)) as a scalar"))
end

mcl_reference(values, model) = Obj(["misclosure_reference" => 1, "model" => model, "values" => values])

function json_string(io::IO, s::AbstractString)
    print(io, '"')
    for c in s
        if c == '"'
            print(io, "\\\"")
        elseif c == '\\'
            print(io, "\\\\")
        elseif c < ' '
            print(io, "\\u", string(UInt32(c); base = 16, pad = 4))
        else
            print(io, c)
        end
    end
    print(io, '"')
end

function mcl_json(io::IO, x)
    if x === nothing
        print(io, "null")
    elseif x isa Bool
        print(io, x ? "true" : "false")
    elseif x isa Integer
        print(io, x)
    elseif x isa AbstractFloat
        throw(ArgumentError("write doubles with mcl_hex, not as JSON numbers"))
    elseif x isa AbstractString
        json_string(io, x)
    elseif x isa AbstractVector{<:Pair}
        print(io, '{')
        for (i, p) in enumerate(x)
            i > 1 && print(io, ',')
            json_string(io, string(first(p)))
            print(io, ':')
            mcl_json(io, last(p))
        end
        print(io, '}')
    elseif x isa AbstractVector
        print(io, '[')
        for (i, v) in enumerate(x)
            i > 1 && print(io, ',')
            mcl_json(io, v)
        end
        print(io, ']')
    else
        throw(ArgumentError("cannot write $(typeof(x)) as JSON"))
    end
end

mcl_json(x) = sprint(mcl_json, x)

function mcl_write_reference(path::AbstractString, values, model)
    open(path, "w") do io
        mcl_json(io, mcl_reference(values, model))
        println(io)
    end
end

end
