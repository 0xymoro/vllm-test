"""A layer that samples the next tokens from the model's outputs."""
import torch
import torch.nn as nn

from vllm.v1.outputs import SamplerOutput
from vllm.v1.sample.metadata import SamplingMetadata
from vllm.v1.sample.ops.topk_topp_sampler import TopKTopPSampler

_SAMPLING_EPS = 1e-5


class Sampler(nn.Module):

    def __init__(self):
        super().__init__()
        self.topk_topp_sampler = TopKTopPSampler()

    def forward(
        self,
        logits: torch.Tensor,
        sampling_metadata: SamplingMetadata,
    ) -> SamplerOutput:
        # Use float32 for the logits.
        logits = logits.to(torch.float32)
        orig_logits = logits

        # Apply temperature.
        logits = self.apply_temperature(logits, sampling_metadata.temperature)
        # Sample the next token.
        sampled = self.sample(logits, sampling_metadata)
        # Use int32 to reduce the tensor size.
        sampled = sampled.to(torch.int32)

        if sampling_metadata.max_num_logprobs > 0:
            logprobs = self.get_logprobs(orig_logits)
            # FIXME: Mask the sampled token_id, get topk logprobs,
            # and concatenate the topk with the sampled token_id.
            topk_logprobs, topk_indices = torch.topk(
                logprobs, sampling_metadata.max_num_logprobs, dim=-1)
            # Use int32 to reduce the tensor size.
            topk_indices = topk_indices.to(torch.int32)
        else:
            topk_logprobs = None
            topk_indices = None

        # NOTE: CPU-GPU synchronization happens here.
        sampler_output = SamplerOutput(
            sampled_token_ids=sampled.tolist(),
            logprob_token_ids=topk_indices,
            logprobs=topk_logprobs,
            prompt_logprob_token_ids=None,
            prompt_logprobs=None,
        )
        return sampler_output

    def apply_temperature(
        self,
        logits: torch.Tensor,
        temp: torch.Tensor,
    ) -> torch.Tensor:
        # Avoid division by zero.
        temp = torch.where(temp < _SAMPLING_EPS, 1.0, temp)
        return logits / temp.unsqueeze(dim=1)

    def greedy_sample(self, logits: torch.Tensor) -> torch.Tensor:
        return logits.argmax(dim=-1).view(-1)

    def sample(
        self,
        logits: torch.Tensor,
        sampling_metadata: SamplingMetadata,
    ) -> torch.Tensor:
        assert not (sampling_metadata.all_greedy
                    and sampling_metadata.all_random)
        if sampling_metadata.all_greedy:
            return self.greedy_sample(logits)

        random_sampled = self.topk_topp_sampler(
            logits,
            sampling_metadata.generators,
            sampling_metadata.no_top_k,
            sampling_metadata.top_k,
            sampling_metadata.no_top_p,
            sampling_metadata.top_p,
        )
        if sampling_metadata.all_random:
            return random_sampled

        greedy_sampled = self.greedy_sample(logits)
        sampled = torch.where(
            sampling_metadata.temperature < _SAMPLING_EPS,
            greedy_sampled,
            random_sampled,
        )
        return sampled

    def get_logprobs(self, logits: torch.Tensor) -> torch.Tensor:
        return torch.log_softmax(logits, dim=-1, dtype=torch.float32)
