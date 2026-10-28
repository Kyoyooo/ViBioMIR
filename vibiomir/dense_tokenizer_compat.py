"""Explicit fast-tokenizer compatibility runner for existing frozen S08 plans.

The five plan-bound source files stay unchanged. The runtime adapter and its
checksum are recorded in each newly created encoder registry/vector batch.
"""
from pathlib import Path

from .dense_models import DenseEncoder
from .io import file_hash
from .retrieval_contracts import require


COMPATIBILITY_ID = 's08-fast-special-mask-v1'
ENCODER_SHA256 = 'f0cd5f5466433639830945a7fbe989d4fad3c43a9f7ce54823109a873bdf9b70'


class SpecialMaskTokenizer:
    """Delegate tokenization; mark only special positions inserted by the model.

    Fast tokenizers cannot prepare_for_model(return_special_tokens_mask=True).
    A negative sentinel through the same input template locates inserted tokens
    without classifying unknown/literal special IDs in the source as padding.
    """

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer

    def __getattr__(self, name):
        return getattr(self.tokenizer, name)

    def __call__(self, *args, **kwargs):
        return self.tokenizer(*args, **kwargs)

    def prepare_for_model(self, ids, pair_ids=None, **kwargs):
        requested = kwargs.pop('return_special_tokens_mask', False)
        feature = self.tokenizer.prepare_for_model(
            ids, pair_ids=pair_ids, return_special_tokens_mask=False, **kwargs)
        if requested:
            require(pair_ids is None and all(i >= 0 for i in ids), 'Compatibility adapter requires one nonnegative token sequence')
            template = self.tokenizer.build_inputs_with_special_tokens([-1] * len(ids)) if kwargs.get('add_special_tokens', True) else [-1] * len(ids)
            mask = [int(i != -1) for i in template]
            require(len(mask) == len(feature['input_ids']) and
                    [i for i, special in zip(feature['input_ids'], mask) if not special] == list(ids),
                    'Special-token template changed token IDs or truncated input')
            feature['special_tokens_mask'] = mask
        return feature


def compatibility_identity():
    return {'id': COMPATIBILITY_ID, 'sha256': file_hash(__file__),
            'encoder_source_sha256': ENCODER_SHA256,
            'policy': 'unchanged token IDs/windows; mask inserted template positions only'}


class CompatibleDenseEncoder(DenseEncoder):
    def __init__(self, *args, **kwargs):
        require(file_hash(Path(__file__).with_name('dense_models.py')) == ENCODER_SHA256,
                'This compatibility adapter targets the original S08 encoder; review it before using another revision')
        super().__init__(*args, **kwargs)
        self.tokenizer = SpecialMaskTokenizer(self.tokenizer)
        self.registry['tokenizer_compatibility'] = compatibility_identity()


def main():
    from . import dense_stage8
    # read_plan still checks the original five source hashes. Only encoder
    # construction changes; source, model, pooling and checkpoints stay bound.
    dense_stage8.DenseEncoder = CompatibleDenseEncoder
    dense_stage8.main()


if __name__ == '__main__':
    main()
