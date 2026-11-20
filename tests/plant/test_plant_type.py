import itertools

import pytest

from agent.plant.personality import Traits
from agent.plant.plant_type import ALL_TYPES, SPROUT, TYPES, plant_type


def test_new_plant_is_a_sprout():
    # 最初はみんな 0.5。まだタイプは決まっていない（#64）
    assert plant_type(Traits()) == SPROUT
    assert SPROUT.key == "sprout"


def test_stays_a_sprout_while_every_axis_is_ordinary():
    # 4 軸とも「ふつう」（0.4 より上・0.6 未満）の間はふたばのまま（B 案）
    assert plant_type(Traits(0.55, 0.45, 0.59, 0.41)) == SPROUT


def test_one_decided_axis_gives_a_type():
    # 1 つでも「ふつう」を出たらタイプが決まる。ふつうの軸は 0.5 以上か未満かで分ける
    t = plant_type(Traits(talkativeness=0.6, affection=0.5, energy=0.45, curiosity=0.55))
    assert t != SPROUT
    assert t == TYPES[(True, True, False, True)]


@pytest.mark.parametrize("highs", list(itertools.product([True, False], repeat=4)))
def test_every_combination_maps_to_its_type(highs):
    # 4 軸を「かなり高い（0.9）／かなり低い（0.1）」にした 16 通りすべてで、その組み合わせのタイプになる
    values = [0.9 if h else 0.1 for h in highs]
    assert plant_type(Traits(*values)) == TYPES[highs]


@pytest.mark.parametrize(
    "traits,key",
    [
        (Traits(0.9, 0.9, 0.9, 0.9), "sunflower"),
        (Traits(0.1, 0.9, 0.1, 0.1), "primrose"),
        (Traits(0.1, 0.1, 0.1, 0.9), "cactus"),
        (Traits(0.1, 0.1, 0.1, 0.1), "moss"),
    ],
)
def test_examples_from_the_issue(traits, key):
    # #64 のコメントの一覧と同じ割り当てになっている
    assert plant_type(traits).key == key


def test_boundaries_use_the_five_levels():
    # 0.6 は「やや高い」なので決まる。0.4 は「やや低い」なので決まる（level() と同じ境目）
    assert plant_type(Traits(0.6, 0.5, 0.5, 0.5)) == TYPES[(True, True, True, True)]
    assert plant_type(Traits(0.4, 0.5, 0.5, 0.5)) == TYPES[(False, True, True, True)]
    # 小数の誤差（0.5 から 0.05 を 2 回引いた値）でも同じように決まる
    assert plant_type(Traits(0.5 - 0.05 - 0.05, 0.5, 0.5, 0.5)) == TYPES[(False, True, True, True)]


def test_there_are_sixteen_types_plus_the_sprout_with_unique_names():
    assert len(TYPES) == 16
    assert set(TYPES) == set(itertools.product([True, False], repeat=4))
    assert len(ALL_TYPES) == 17 and ALL_TYPES[0] == SPROUT
    # 名前・英語名（イラストのファイル名）が重ならない
    assert len({t.key for t in ALL_TYPES}) == 17
    assert len({t.name for t in ALL_TYPES}) == 17
    # 英語名はファイル名に使うので、小文字の英字だけ
    assert all(t.key.isascii() and t.key.isalpha() and t.key.islower() for t in ALL_TYPES)
    assert all(t.emoji and t.description for t in ALL_TYPES)


def test_every_type_has_a_detail_of_two_or_three_sentences():
    # ダッシュボードの結果画面に出す長めの説明（仕様 §6.6）。短すぎず長すぎず
    for t in ALL_TYPES:
        assert 40 <= len(t.detail) <= 140, t.key
        assert 2 <= t.detail.count("。") <= 3, t.key


def test_type_texts_do_not_borrow_other_test_names():
    # 名前も説明もオリジナル（ほかの性格診断の名前を使わない、#64）
    for t in ALL_TYPES:
        for word in ("MBTI", "ラブタイプ", "16Personalities"):
            assert word not in t.name + t.description + t.detail
