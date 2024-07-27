/*
    Tasks:

    task [automatic] ... endtask
*/

task automatic task_a;
input a, b;
output c, d;
begin
    c = a & b;
    d = a | b;
end
endtask

function [4:0] add_3;
    input [3:0] i1, i2, i3;
    add_3 = i1 + i2 + i3;
endfunction

task task_b;
input [3:0] a, b, c;
output reg [4:0] s;
begin
    s = add_3(a, b, c);
end
endtask

module top (
    input clock,
    input [3:0] i1, i2, i3,
    input j1, j2,
    output o_scalar
);

    wire [1:0] oa;
    reg [4:0] ob;

    assign o_scalar = ^~oa | ^~ ob;

    always @(*) begin
        task_a(j1, j2, oa[0], oa[1]);
    end

    always @(posedge clock) begin
        task_b(i1, i2, i3, ob);
    end

endmodule
