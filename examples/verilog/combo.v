module combo (
    input a, b, c, d, e,
    output z);
    assign z = ((a & b) | (c ^ d) & ~e);
endmodule