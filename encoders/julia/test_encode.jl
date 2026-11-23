# Round-trip test of the Julia encoder. Run: julia test_encode.jl [out.json]
# The optional argument writes the encoded test table, for the cross-language
# test in tests/.

include(joinpath(@__DIR__, "ModelcheckEncode.jl"))
using .ModelcheckEncode
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
    x = mc_unhex.(AWKWARD)
    @test mc_hex.(x) == AWKWARD
    @test mc_hex(-0.0) == "8000000000000000"
    @test mc_hex(floatmax(Float64)) == "7fefffffffffffff"
    @test mc_hex(nextfloat(0.0)) == "0000000000000001"
    @test isnan(x[10]) && isnan(x[12])
    t = mc_table(["country" => ["c1", "c2", missing], "n" => [1, missing, 3],
                  "ok" => [true, missing, false], "value" => [0.1, missing, -0.0]])
    cols = last(t[1])
    @test [last(c[2]) for c in cols] == ["string", "integer", "logical", "double"]
    @test last(cols[4][3]) == Any["3fb999999999999a", "7ff00000000007a2", "8000000000000000"]
    @test last(cols[1][3])[3] === nothing
    @test mc_json(mc_scalar(22)) == "{\"integer\":22}"
    @test mc_json(mc_scalar(0.1)) == "{\"double\":\"3fb999999999999a\"}"
    @test mc_json(mc_scalar("a\"b")) == "{\"string\":\"a\\\"b\"}"
    @test mc_scalar(missing) === nothing
end

if length(ARGS) >= 1
    mc_write_reference(ARGS[1],
        ["awkward" => mc_table(["bits" => AWKWARD, "value" => mc_unhex.(AWKWARD)]),
         "mixed" => mc_table(["country" => ["c1", "c2", missing], "n" => [1, missing, 3],
                              "ok" => [true, missing, false], "value" => [0.1, missing, -0.0]])],
        ["name" => "encoder test", "version" => "1"])
end
println("Julia encoder: all round-trip checks passed")
