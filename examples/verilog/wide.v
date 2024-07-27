module wide(input clk, input reset, output [99:0] value);

  reg [99:0] reg0;

  always @(posedge clk)
  begin
    if (reset)
      reg0 <= 0;
    else
      reg0 <= {reg0[98:0], reg0[99]};
  end

  assign value = reg0;

endmodule
