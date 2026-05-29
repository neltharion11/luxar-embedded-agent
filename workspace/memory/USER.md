Linker error in project 1: section .init LMA overlaps section .data LMA. Need to update linker script to include .init section in .text output section, and adjust data LMA to use _etext properly.
