/*
    Looping statements:

    (forever), repeat, (while), for

    These statements provide a means of controlling the execution of
    a statement zero, one, or more times.
    Because "forever" and "while" are rarely used in synthesizable code,
    they are not included in this example.
*/

module top (
    input clock,
    input [7:0] repeat_input,
    output [31:0] o
);
    wire [7:0] repeat_output;
    wire [31:0] for_output;

    repeat_statement s2 (.clock(clock), .input_a(repeat_input[7:4]),
        .input_b(repeat_input[3:0]), .result(repeat_output));
    for_statement s4 (.clock(clock), .result(for_output));

    assign o = repeat_output & for_output;
endmodule

module repeat_statement (
    input clock,
    input [3:0] input_a,
    input [3:0] input_b,
    output result
);
    reg [3:0] tempreg;
    parameter size = 2;

    always @(posedge clock) begin : block_name
        reg [3:0] shift_right, shift_left;
        tempreg = 0;
        shift_left = input_a;
        shift_right = input_b;
        repeat (size) begin
            if (shift_left[1])
                tempreg = tempreg + shift_right;
            shift_left = input_a << 1;
            shift_right = input_b >> 1;
        end
    end
    assign result = ^~tempreg;
endmodule

module for_statement (
    input clock,
    output [31:0] result
);
    reg [31:0] tempreg, i;
    parameter loop_count = 3;

    always @(posedge clock) begin
        for (i = 0; i < loop_count; i = i + 1)
            tempreg = tempreg + loop_count;
    end
    assign result = tempreg;
endmodule
