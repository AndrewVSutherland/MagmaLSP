// Regression corpus of intrinsic-declaration grammar edge cases (CLAUDE.md §6).
// Used by tests/test_package.py to lock in the extractor's behavior.

intrinsic Simple(x::RngIntElt) -> RngIntElt
{ The simplest case. }
    return x;
end intrinsic;

intrinsic WithOptionals(x::RngIntElt, S::SeqEnum[RngIntElt] : Al := "Default", Bound := 0)
    -> RngIntElt, BoolElt
{ Multi-line header with optional params and multiple return types. }
    return x, true;
end intrinsic;

intrinsic ExprDefault(model3::Crv : C3 := Curve(model3)) -> Crv
{ Optional-parameter default is an expression. }
    return model3;
end intrinsic;

intrinsic '+'(x::AlgMatLie, y::AlgMatLie) -> AlgMatLie
{ Operator intrinsic with a quoted name. }
    return x;
end intrinsic;

intrinsic AssignNames(~C::AlgClff, S::SeqEnum[MonStgElt])
{ Procedure: reference arg, no return type. }
    ;
end intrinsic;

intrinsic Wildcard(x::., y::Any) -> BoolElt
{ Wildcard and Any argument types. }
    return true;
end intrinsic;

intrinsic Empty(x::RngIntElt) -> RngIntElt
{}
    return x;
end intrinsic;

intrinsic Dittoed(x::RngIntElt) -> RngIntElt
{ This doc is shared by the next overload. }
    return x;
end intrinsic;

intrinsic Dittoed(x::FldReElt) -> FldReElt
{"} //"
    return x;
end intrinsic;
