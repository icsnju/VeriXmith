module inner(
    input clk, input reset,
    input sub_i, output sub_o
);
    reg reg0;

    always @(posedge clk) begin
        if (reset) begin
            reg0 <= 0;
        end else begin
            reg0 <= sub_i;
        end
    end

    assign sub_o = reg0;

endmodule

module gen (
    input clk, input reset,
    input [3:0] in,
    output [3:0] out
);

    reg [1:0] reg1[1:0];
    wire prev[1:0][1:0];

    // input -> sub_output_vec
    wire [3:0] sub_output_vec;
    inner l [3:0] (.clk(clk), .reset(reset), .sub_i(in), .sub_o(sub_output_vec));

    // sub_output_vec -> prev
    genvar i, j;
    generate
        for (i = 0; i < 2; i = i + 1) begin:a
            for (j = 0; j < 2; j = j + 1) begin:b
                assign prev[i][j] = sub_output_vec[i*2+j];
            end
        end
    endgenerate

    // prev -> reg1
    always @(posedge clk) begin
        if (reset) begin
            reg1[0] <= 2'b0;
            reg1[1] <= 2'b0;
        end else begin
            reg1[0] <= {prev[0][1], prev[0][0]};
            reg1[1] <= {prev[1][1], prev[1][0]};
        end
    end

    // reg1 -> output
    assign {out[1], out[0]} = reg1[0];
    assign {out[3], out[2]} = reg1[1];

endmodule