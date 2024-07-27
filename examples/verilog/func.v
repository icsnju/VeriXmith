/*
    Functions:

    function [automatic] ... endfunction
*/

module top (
    input clock,
    input [3:0] a, b, c,
    output [4:0] s
);

    reg [4:0] r_sum;

    function [4:0] add_3;
        input [3:0] i1, i2, i3;
        add_3 = i1 + i2 + i3;
    endfunction

    assign s = r_sum;

    always @(posedge clock) begin
        r_sum <= add_3(a, b, c);
    end
endmodule