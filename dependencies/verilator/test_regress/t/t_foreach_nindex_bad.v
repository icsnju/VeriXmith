// DESCRIPTION: Verilator: Verilog Test module
//
// This file ONLY is placed under the Creative Commons Public Domain, for
// any use, without warranty, 2022 by Wilson Snyder.
// SPDX-License-Identifier: CC0-1.0

module t (/*AUTOARG*/);

   int array[2][2];

   initial begin
      foreach (array[i, j, badk, badl]);  // bad

      $stop;
   end

endmodule
