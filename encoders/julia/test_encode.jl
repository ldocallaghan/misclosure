# Round-trip test of the Julia encoder. Run: julia test_encode.jl [out.json]
# The optional argument writes the encoded test table, for the cross-language
# test in tests/.

include(joinpath(@__DIR__, "MisclosureEncode.jl"))
using .MisclosureEncode
using Test

const AWKWARD = [
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
    "3fb999999999999a",  # 0.1
]

@testset "Julia encoder" begin
    x = mcl_unhex.(AWKWARD)
    @test mcl_hex.(x) == AWKWARD
    @test mcl_hex(-0.0) == "8000000000000000"
    @test mcl_hex(floatmax(Float64)) == "7fefffffffffffff"
    @test mcl_hex(nextfloat(0.0)) == "0000000000000001"
    @test isnan(x[10]) && isnan(x[12])
    t = mcl_table(["country" => ["c1", "c2", missing], "n" => [1, missing, 3],
                  "ok" => [true, missing, false], "value" => [0.1, missing, -0.0]])
    cols = last(t[1])
    @test [last(c[2]) for c in cols] == ["string", "integer", "logical", "double"]
    @test last(cols[4][3]) == Any["3fb999999999999a", "7ff00000000007a2", "8000000000000000"]
    @test last(cols[1][3])[3] === nothing
    @test mcl_json(mcl_scalar(22)) == "{\"integer\":22}"
    @test mcl_json(mcl_scalar(0.1)) == "{\"double\":\"3fb999999999999a\"}"
    @test mcl_json(mcl_scalar("a\"b")) == "{\"string\":\"a\\\"b\"}"
    @test mcl_scalar(missing) === nothing
end

if length(ARGS) >= 1
    mcl_write_reference(ARGS[1],
        ["awkward" => mcl_table(["bits" => AWKWARD, "value" => mcl_unhex.(AWKWARD)]),
         "mixed" => mcl_table(["country" => ["c1", "c2", missing], "n" => [1, missing, 3],
                              "ok" => [true, missing, false], "value" => [0.1, missing, -0.0]])],
        ["name" => "encoder test", "version" => "1"])
end
println("Julia encoder: all round-trip checks passed")
