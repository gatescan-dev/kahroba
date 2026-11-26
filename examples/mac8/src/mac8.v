// 8x8 signed multiply-accumulate with a pipeline register and 20-bit accumulator.
module mac8 (
    input  wire               clk,
    input  wire               rst,
    input  wire               valid,
    input  wire               clear,
    input  wire signed [7:0]  a,
    input  wire signed [7:0]  b,
    output reg  signed [19:0] acc,
    output reg                out_valid
);
    reg signed [15:0] prod;
    reg               pv, pclr;

    always @(posedge clk) begin
        if (rst) begin
            prod <= 16'sd0; pv <= 1'b0; pclr <= 1'b0;
            acc <= 20'sd0; out_valid <= 1'b0;
        end else begin
            prod <= a * b;
            pv   <= valid;
            pclr <= clear;
            out_valid <= pv;
            if (pclr)
                acc <= pv ? {{4{prod[15]}}, prod} : 20'sd0;
            else if (pv)
                acc <= acc + {{4{prod[15]}}, prod};
        end
    end
endmodule
