import pytest
from lensless.io import load_data

try:
    import pycsou
    from lensless import GradientDescent, NesterovGradientDescent, FISTA, ADMM, APGD, APGDPriors

    pycsou_available = True

except ImportError:
    from lensless import GradientDescent, NesterovGradientDescent, FISTA, ADMM

    pycsou_available = False


try:
    import torch
    from lensless import UnrolledFISTA, UnrolledADMM

    torch_is_available = True
    torch.autograd.set_detect_anomaly(True)
    trainable_algos = [UnrolledFISTA, UnrolledADMM]
except ImportError:
    torch_is_available = False
    trainable_algos = []


psf_fp = "data/psf/tape_rgb.png"
data_fp = "data/raw_data/thumbs_up_rgb.png"
dtypes = ["float32", "float64"]
downsample = 16
_n_iter = 5

# classical algorithms
standard_algos = [GradientDescent, NesterovGradientDescent, FISTA, ADMM]


@pytest.mark.parametrize("algorithm", standard_algos)
def test_recon_numpy(algorithm):
    for gray in [True, False]:
        for dtype in dtypes:
            psf, data = load_data(
                psf_fp=psf_fp,
                data_fp=data_fp,
                downsample=downsample,
                plot=False,
                gray=gray,
                dtype=dtype,
                torch=False,
            )
            recon = algorithm(psf, dtype=dtype)
            recon.set_data(data)
            res = recon.apply(n_iter=_n_iter, disp_iter=None, plot=False)
            assert len(psf.shape) == 4
            assert psf.shape[3] == (1 if gray else 3)
            assert res.dtype == psf.dtype, f"Got {res.dtype}, expected {dtype}"


@pytest.mark.parametrize("algorithm", standard_algos + trainable_algos)
def test_recon_torch(algorithm):
    if not torch_is_available:
        return
    for gray in [True, False]:
        for dtype in dtypes:
            psf, data = load_data(
                psf_fp=psf_fp,
                data_fp=data_fp,
                downsample=downsample,
                plot=False,
                gray=gray,
                dtype=dtype,
                torch=True,
            )
            recon = algorithm(psf, dtype=dtype, n_iter=_n_iter)
            recon.set_data(data)
            res = recon.apply(disp_iter=None, plot=False)
            assert recon._n_iter == _n_iter
            assert len(psf.shape) == 4
            assert psf.shape[3] == (1 if gray else 3)
            assert res.dtype == psf.dtype, f"Got {res.dtype}, expected {dtype}"


def test_apgd():
    if pycsou_available:
        for gray in [True, False]:
            for dtype in dtypes:
                for diff_penalty in [APGDPriors.L2, None]:
                    for prox_penalty in [APGDPriors.NONNEG, APGDPriors.L1]:
                        psf, data = load_data(
                            psf_fp=psf_fp,
                            data_fp=data_fp,
                            downsample=downsample,
                            plot=False,
                            gray=gray,
                            dtype=dtype,
                            torch=False,
                        )
                        recon = APGD(
                            psf,
                            dtype=dtype,
                            prox_penalty=prox_penalty,
                            diff_penalty=diff_penalty,
                            rel_error=None,
                        )
                        recon.set_data(data)
                        res = recon.apply(n_iter=_n_iter, disp_iter=None, plot=False)
                        assert psf.shape[3] == (1 if gray else 3)
                        assert res.dtype == psf.dtype, f"Got {res.dtype}, expected {dtype}"
    else:
        print("Pycsou not installed. Skipping APGD test.")


#  trainable algorithms
@pytest.mark.parametrize("algorithm", trainable_algos)
def test_trainable_recon(algorithm):
    if torch_is_available:
        for dtype, torch_type in [("float32", torch.float32), ("float64", torch.float64)]:
            psf = torch.rand(1, 32, 64, 3, dtype=torch_type)
            data = torch.rand(2, 1, 32, 64, 3, dtype=torch_type)
            recon = UnrolledFISTA(psf, n_iter=_n_iter, dtype=dtype)

            assert (
                next(recon.parameters(), None) is not None
            ), f"{algorithm.__name__} has no trainable parameters"

            res = recon.batch_call(data)
            loss = torch.mean(res)
            loss.backward()

            assert (
                data.shape[0] == res.shape[0]
            ), f"Batch dimension changed: got {res.shape[0]} expected {data.shape[0]}"

            assert len(psf.shape) == 4
            print(res.shape)
            assert res.shape[4] == 3, "Input in HWC format but output CHW format"


@pytest.mark.parametrize("algorithm", trainable_algos)
def test_trainable_ind(algorithm):
    if not torch_is_available:
        return
    for dtype, torch_type in [("float32", torch.float32), ("float64", torch.float64)]:
        psf = torch.rand(2, 35, 61, 3, dtype=torch_type)
        data1 = torch.rand(5, 1, 35, 61, 3, dtype=torch_type)
        data2 = torch.rand(1, 1, 35, 61, 3, dtype=torch_type)
        data2[0, 0, ...] = data1[0, 0, ...]

        recon = algorithm(psf, dtype=dtype, n_iter=_n_iter)
        res1 = recon.batch_call(data1)
        res2 = recon.batch_call(data2)

        assert torch.allclose(res1[0, 0, ...], res2[0, 0, ...])
        assert recon._n_iter == _n_iter
        assert len(psf.shape) == 4
        assert res1.dtype == psf.dtype, f"Got {res1.dtype}, expected {dtype}"
