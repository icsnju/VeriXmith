module counter (
    input clock,
    input reset,
    output [3:0] value
);
    reg [3:0] reg0;

    always @(posedge clock) begin
        if (reset) begin
            reg0 <= 0;
        end else begin
            reg0 <= reg0 + 1;
        end
    end

    assign value = reg0;
endmodule