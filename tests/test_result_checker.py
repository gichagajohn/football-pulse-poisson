from backend.result_checker import ticket_outcome


def test_void_legs_do_not_spoil_a_winning_singles_ticket():
    # 2 wins + 1 void is a winning day: the void was never staked.
    assert ticket_outcome(["win", "win", "void"]) == "win"


def test_mixed_when_wins_and_losses_coexist():
    assert ticket_outcome(["win", "loss"]) == "mixed"


def test_all_loss_and_all_void():
    assert ticket_outcome(["loss", "loss"]) == "loss"
    assert ticket_outcome(["void", "void"]) == "void"
