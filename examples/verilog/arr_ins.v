// array of instances example

module bit_adder(
    input a, b, c,
    output s, c_out
);
    assign s = a ^ b ^ c;
    assign c_out = a & b | b & c | a & c;
endmodule

module adder(
    input [3:0] a, input [3:0] b, input c_in,
    output [3:0] sum, output c_out
);
    wire [2:0] carry;

    bit_adder adder_4 [3:0] (
        .a(a), .b(b), .c({carry, c_in}),
        .s(sum), .c_out({c_out, carry}) );

endmodule